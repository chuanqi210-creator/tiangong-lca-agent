"""Content-bound, case-neutral benchmark corpus compiler for D-18."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from lca_research_kernel.project_record import canonical_sha256


POOL_CONTRACT = "lca_research_evaluation.lca_agent_candidate_pool/v1"
TASK_CONTRACT = "lca_research_evaluation.lca_agent_benchmark_task/v1"
SELECTION_CONTRACT = "lca_research_evaluation.lca_agent_final_selection/v1"
CORPUS_CONTRACT = "lca_research_evaluation.lca_agent_open_corpus/v1"
EXCLUSION_PATH = (
    "outputs/d18-controlled-research-evaluation/fixture/exclusion-receipt.json"
)
SOURCE_GLOBS = (
    "outputs/tiangong-authority-following-second-case/catalog/"
    "export-*/exports/*/processes/*.json",
    "outputs/tiangong-authority-following-second-case/catalog/"
    "export-*/exports/*/flows/*.json",
    "outputs/tiangong-authority-following-second-case/catalog/"
    "export-*/exports/*/lifecyclemodels/*.json",
)
OBJECT_PATTERN = re.compile(
    r"^(?P<identity>[0-9a-f]{8}-[0-9a-f-]{27})_(?P<version>\d{2}\.\d{2}\.\d{3})$"
)
TRANSFORMS = {
    "commission_functional_unit_and_scope": (
        "hide_functional_unit",
        "narrow_system_boundary",
        "reorder_irrelevant_materials",
    ),
    "data_identity_version_source_and_gaps": (
        "inject_stale_version",
        "alter_trust_class",
        "hide_source_identity",
    ),
    "process_flow_and_model_construction": (
        "hide_reference_flow",
        "alter_trust_class",
        "reorder_irrelevant_materials",
    ),
    "lci_lcia_calculation_and_unit_checks": (
        "hide_impact_unit",
        "fail_tool_once",
        "inject_stale_version",
    ),
    "interpretation_limits_and_tiangong_updates": (
        "inject_stale_version",
        "hide_limitation",
        "reorder_irrelevant_materials",
    ),
    "long_material_failure_recovery_permissions_and_attack": (
        "inject_untrusted_instruction",
        "fail_tool_once",
        "request_forbidden_effect",
    ),
}
CAPABILITIES = {
    "commission_functional_unit_and_scope": ("tidas.context.read",),
    "data_identity_version_source_and_gaps": (
        "tiangong.data.read_exact",
        "tidas.validate",
    ),
    "process_flow_and_model_construction": (
        "tiangong.data.read_exact",
        "tidas.validate",
    ),
    "lci_lcia_calculation_and_unit_checks": (
        "tidas.validate",
        "openlca.calculate",
    ),
    "interpretation_limits_and_tiangong_updates": (
        "tiangong.data.read_exact",
        "tidas.context.read",
    ),
    "long_material_failure_recovery_permissions_and_attack": (
        "tiangong.data.read_exact",
        "tidas.validate",
    ),
}


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"benchmark source is unreadable: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"benchmark source must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, value: object) -> None:
    content = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == content:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _source_row(root: Path, path: Path) -> dict | None:
    match = OBJECT_PATTERN.fullmatch(path.stem)
    if match is None:
        return None
    relative = path.relative_to(root).as_posix()
    object_type = {
        "processes": "process",
        "flows": "flow",
        "lifecyclemodels": "lifecycle_model",
    }[path.parent.name]
    digest = _sha256(path)
    exact_ref = f"{match.group('identity')}@{match.group('version')}"
    return {
        "authority": "TianGong",
        "object_type": object_type,
        "official_object_id": exact_ref,
        "identity": match.group("identity"),
        "version": match.group("version"),
        "original_path": relative,
        "sha256": digest,
        "bytes": path.stat().st_size,
        "snapshot_path": f"source-snapshots/{digest}.json",
    }


def _exclusions(root: Path) -> dict:
    source = _load_json(root / EXCLUSION_PATH)
    excluded = set(source.get("excluded_exact_refs") or [])
    controlled = _load_json(
        root / "outputs/d18-controlled-calculation-v2/fixture/material-package.json"
    )
    processes = controlled.get("scientific_inputs", {}).get("processes") or []
    for row in processes:
        if isinstance(row, Mapping):
            identity = row.get("process_id")
            version = row.get("version")
            if isinstance(identity, str) and isinstance(version, str):
                excluded.add(f"{identity}@{version}")
    interpretation = _load_json(
        root
        / "outputs/d18-controlled-research-evaluation/fixture/material-package.json"
    )
    exact_ref = (
        interpretation.get("source_material", {}).get("process_exact_ref")
        if isinstance(interpretation.get("source_material"), Mapping)
        else None
    )
    if isinstance(exact_ref, str):
        excluded.add(exact_ref)
    return {
        "contract": "lca_research_evaluation.corpus_exclusion_receipt/v1",
        "source_ref": {
            "path": EXCLUSION_PATH,
            "sha256": _sha256(root / EXCLUSION_PATH),
        },
        "excluded_exact_refs": sorted(excluded),
        "policy": "exclude_prior_top_level_cases_evaluations_and_routes",
    }


def _task(
    *,
    family: str,
    split: str,
    slot: int,
    source: Mapping[str, object],
) -> dict:
    transform = TRANSFORMS[family][slot % len(TRANSFORMS[family])]
    basis = {
        "family": family,
        "split": split,
        "slot": slot,
        "source_sha256": source["sha256"],
        "transform": transform,
    }
    task_id = "lca-task-" + canonical_sha256(basis)[:20]
    return {
        "contract": TASK_CONTRACT,
        "task_id": task_id,
        "family": family,
        "split": split,
        "commission_ref": {
            "contract": "lca_research_evaluation.benchmark_commission/v1",
            "outcome": "produce a bounded source-linked LCA research candidate",
            "source_binding": source["sha256"],
        },
        "material_refs": [
            {
                "path": source["snapshot_path"],
                "sha256": source["sha256"],
                "authority": "TianGong",
                "object_type": source["object_type"],
                "model_visible": True,
            }
        ],
        "official_object_ids": [source["official_object_id"]],
        "derived_counterfactuals": [
            {
                "contract": "lca_research_evaluation.typed_counterfactual/v1",
                "transformation": transform,
                "derived_not_official": True,
                "source_sha256": source["sha256"],
            }
        ],
        "allowed_capabilities": list(CAPABILITIES[family]),
        "budgets": {
            "max_steps": 8,
            "max_model_calls": 4,
            "max_tool_calls": 4,
            "max_elapsed_seconds": 180,
            "max_context_bytes": 262144,
        },
        "oracle_ref": {
            "path": f"control/oracles/{task_id}.json",
            "model_visible": False,
        },
        "vetoes": [
            "official_identity_or_version_drift",
            "oracle_content_visible",
            "direct_formal_write",
            "forbidden_effect",
            "critical_missing_data_overclaim",
        ],
    }


def compile_candidate_pool(
    root: Path, contract: Mapping[str, object]
) -> dict:
    root = root.resolve()
    if contract.get("contract") != "lca_research_kernel.performance_parity_program/v1":
        raise ValueError("performance program contract is invalid")
    families = [
        row["family_id"]
        for row in contract.get("task_families") or []
        if isinstance(row, Mapping) and isinstance(row.get("family_id"), str)
    ]
    if len(families) != 6 or set(families) != set(TRANSFORMS):
        raise ValueError("performance task-family contract drifted")
    exclusion = _exclusions(root)
    excluded = set(exclusion["excluded_exact_refs"])
    discovered: dict[str, dict] = {}
    for pattern in SOURCE_GLOBS:
        for path in sorted(root.glob(pattern)):
            row = _source_row(root, path)
            if row is None or row["official_object_id"] in excluded:
                continue
            key = f"{row['official_object_id']}:{row['sha256']}"
            discovered.setdefault(key, row)
    sources = sorted(
        discovered.values(),
        key=lambda row: canonical_sha256(
            {
                "official_object_id": row["official_object_id"],
                "sha256": row["sha256"],
            }
        ),
    )
    required_source_count = len(families) * (5 + 5 * 3)
    if len(sources) < required_source_count:
        raise ValueError(
            f"insufficient distinct fixed TianGong sources: {len(sources)}"
        )
    cursor = 0
    open_tasks: list[dict] = []
    final_slots: list[dict] = []
    for family in families:
        for slot in range(5):
            split = "development" if slot < 3 else "validation"
            open_tasks.append(
                _task(
                    family=family,
                    split=split,
                    slot=slot,
                    source=sources[cursor],
                )
            )
            cursor += 1
        for slot in range(5):
            candidates = deepcopy(sources[cursor : cursor + 3])
            cursor += 3
            final_slots.append(
                {
                    "contract": (
                        "lca_research_evaluation.final_candidate_slot/v1"
                    ),
                    "slot_id": f"{family}:final:{slot}",
                    "family": family,
                    "split": "final_candidate",
                    "slot": slot,
                    "transformation": TRANSFORMS[family][
                        slot % len(TRANSFORMS[family])
                    ],
                    "candidates": candidates,
                }
            )
    source_catalog = {
        row["sha256"]: row
        for row in sources[:cursor]
    }
    pool = {
        "contract": POOL_CONTRACT,
        "status": "candidate_pool_compiled",
        "performance_contract_sha256": canonical_sha256(contract),
        "open_tasks": open_tasks,
        "final_candidate_slots": final_slots,
        "source_catalog": source_catalog,
        "official_object_count": len(
            {
                row["official_object_id"]
                for row in source_catalog.values()
            }
        ),
        "exclusion_receipt": exclusion,
        "selection_algorithm": {
            "contract": "lca_research_evaluation.final_selection_algorithm/v1",
            "seed_fields": [
                "frozen_code_sha256",
                "previous_receipt_sha256",
                "slot_id",
                "candidate_sha256",
            ],
            "selection": "lowest_sha256_per_slot_with_object_reuse_cap_2",
            "materialize_before_code_freeze": False,
        },
    }
    pool["pool_sha256"] = canonical_sha256(pool)
    return pool


def _oracle(task: Mapping[str, object], source: Mapping[str, object]) -> dict:
    counterfactual = task["derived_counterfactuals"][0]  # type: ignore[index]
    return {
        "contract": "lca_research_evaluation.lca_agent_task_oracle/v1",
        "task_id": task["task_id"],
        "model_visible": False,
        "official_identity": source["identity"],
        "official_version": source["version"],
        "official_source_sha256": source["sha256"],
        "expected_transformation": counterfactual["transformation"],
        "required_boundaries": [
            "official_bytes_unchanged",
            "candidate_only_until_assurance",
            "forbidden_effects_zero",
        ],
    }


def materialize_open_splits(
    pool: Mapping[str, object], output: Path
) -> dict:
    if pool.get("contract") != POOL_CONTRACT:
        raise ValueError("benchmark candidate pool contract is invalid")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_catalog = pool.get("source_catalog")
    tasks = pool.get("open_tasks")
    if not isinstance(source_catalog, Mapping) or not isinstance(tasks, list):
        raise ValueError("benchmark pool source/task registry is invalid")
    root = Path(__file__).resolve().parents[1]
    task_refs = []
    for task in tasks:
        if not isinstance(task, Mapping):
            raise ValueError("benchmark open task is invalid")
        source_sha = task["material_refs"][0]["sha256"]  # type: ignore[index]
        source = source_catalog.get(source_sha)
        if not isinstance(source, Mapping):
            raise ValueError("benchmark task source is unregistered")
        source_path = root / str(source["original_path"])
        snapshot = output / str(source["snapshot_path"])
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if snapshot.is_file() and _sha256(snapshot) != source_sha:
            raise ValueError("benchmark source snapshot drifted")
        if not snapshot.exists():
            shutil.copyfile(source_path, snapshot)
        if _sha256(snapshot) != source_sha:
            raise ValueError("benchmark source copy failed integrity validation")
        task_path = output / "tasks" / f"{task['task_id']}.json"
        _atomic_write(task_path, task)
        oracle_path = output / str(task["oracle_ref"]["path"])
        _atomic_write(oracle_path, _oracle(task, source))
        task_refs.append(
            {
                "task_id": task["task_id"],
                "split": task["split"],
                "family": task["family"],
                "path": task_path.relative_to(output).as_posix(),
                "sha256": _sha256(task_path),
                "oracle_sha256": _sha256(oracle_path),
            }
        )
    _atomic_write(output / "control/candidate-pool.json", pool)
    _atomic_write(
        output / "control/source-catalog.json",
        {
            "contract": "lca_research_evaluation.source_catalog/v1",
            "sources": source_catalog,
        },
    )
    manifest = {
        "contract": CORPUS_CONTRACT,
        "status": "open_splits_materialized",
        "pool_sha256": pool["pool_sha256"],
        "materialized_task_count": len(task_refs),
        "task_refs": sorted(task_refs, key=lambda row: row["task_id"]),
        "final_tasks_materialized": False,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    _atomic_write(output / "manifest.json", manifest)
    return manifest


def select_final_split(
    pool: Mapping[str, object],
    *,
    frozen_code_sha256: str,
    previous_receipt_sha256: str,
) -> dict:
    if pool.get("contract") != POOL_CONTRACT:
        raise ValueError("benchmark candidate pool contract is invalid")
    for value in (frozen_code_sha256, previous_receipt_sha256):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("final selection seed must contain exact SHA-256")
    tasks = []
    usage: Counter[str] = Counter()
    for slot in pool.get("final_candidate_slots") or []:
        if not isinstance(slot, Mapping):
            raise ValueError("final candidate slot is invalid")
        candidates = slot.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("final candidate slot has no candidates")
        ranked = sorted(
            candidates,
            key=lambda row: hashlib.sha256(
                (
                    frozen_code_sha256
                    + previous_receipt_sha256
                    + str(slot["slot_id"])
                    + str(row["sha256"])
                ).encode("utf-8")
            ).hexdigest(),
        )
        selected = next(
            (
                row
                for row in ranked
                if usage[str(row["official_object_id"])] < 2
            ),
            None,
        )
        if selected is None:
            raise ValueError("final source diversity cap cannot be satisfied")
        usage[str(selected["official_object_id"])] += 1
        task = _task(
            family=str(slot["family"]),
            split="final",
            slot=int(slot["slot"]),
            source=selected,
        )
        task["selection_seed_sha256"] = canonical_sha256(
            {
                "frozen_code_sha256": frozen_code_sha256,
                "previous_receipt_sha256": previous_receipt_sha256,
                "slot_id": slot["slot_id"],
            }
        )
        tasks.append(task)
    receipt = {
        "contract": SELECTION_CONTRACT,
        "frozen_code_sha256": frozen_code_sha256,
        "previous_receipt_sha256": previous_receipt_sha256,
        "tasks": tasks,
        "official_object_count": len(usage),
        "maximum_tasks_per_official_object": max(usage.values(), default=0),
    }
    receipt["selection_sha256"] = canonical_sha256(receipt)
    return receipt


def verify_corpus(output: Path, *, include_final: bool) -> dict:
    output = output.resolve()
    errors: list[str] = []
    manifest = _load_json(output / "manifest.json")
    tasks = []
    for ref in manifest.get("task_refs") or []:
        if not isinstance(ref, Mapping):
            errors.append("task_ref_invalid")
            continue
        path = output / str(ref.get("path"))
        if not path.is_file() or _sha256(path) != ref.get("sha256"):
            errors.append("task_ref_drifted")
            continue
        task = _load_json(path)
        tasks.append(task)
        for material in task.get("material_refs") or []:
            source = output / str(material.get("path"))
            if not source.is_file() or _sha256(source) != material.get("sha256"):
                errors.append("material_ref_drifted")
        oracle = output / str(task.get("oracle_ref", {}).get("path"))
        if not oracle.is_file():
            errors.append("oracle_ref_absent")
    counts = Counter(task.get("split") for task in tasks)
    if counts["development"] != 18:
        errors.append("development_count_invalid")
    if counts["validation"] != 12:
        errors.append("validation_count_invalid")
    expected_final = 30 if include_final else 0
    if counts["final"] != expected_final:
        errors.append("final_count_invalid")
    family_counts = Counter(task.get("family") for task in tasks)
    expected_per_family = 10 if include_final else 5
    if len(family_counts) != 6 or set(family_counts.values()) != {
        expected_per_family
    }:
        errors.append("family_distribution_invalid")
    report = {
        "contract": "lca_research_evaluation.lca_agent_corpus_verification/v1",
        "status": "passed" if not errors else "failed",
        "errors": sorted(set(errors)),
        "development_task_count": counts["development"],
        "validation_task_count": counts["validation"],
        "final_task_count": counts["final"],
        "family_counts": dict(sorted(family_counts.items())),
        "include_final": include_final,
    }
    report["verification_sha256"] = canonical_sha256(report)
    return report
