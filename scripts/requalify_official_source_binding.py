#!/usr/bin/env python3
"""Requalify the official-source adapter without a new network or model call."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_kernel.official_public_source import (
    OfficialPublicSourceCapability,
)
from lca_research_kernel.official_source_binding import (
    validate_official_source_observation,
)
from lca_research_kernel.runtime_environment import RuntimeEnvironmentContract
from lca_research_kernel.runtime_environment_health import (
    verify_matrix_receipts,
)


BASELINE_COMMIT = "495f402232a8e3db8b435472fecedd18d7ecb982"
OLD_MATRIX = Path(
    "outputs/runtime-environment/runtime-readiness-matrix-source-binding-v6.json"
)
OLD_MATRIX_SHA256 = (
    "0c15abf218019a06ab279b6f7af6a77da82f600d179f978475e2ba2bd79385a7"
)
OLD_SOURCE_LOCK_SHA256 = (
    "b2e2fdc85ac8ba53bbcf95d88b569c88ffb6048cafccdf2a7dada5b9ffa8a8fd"
)
OLD_SUMMARY = Path(
    "outputs/runtime-environment/runtime-health-summary-source-binding-v6.json"
)
CACHE_ROOT = Path(
    "outputs/real-question-optimization/73101eec6999b27f/staged-regression-v10/"
    "preflight/adjudication/official-public-source-cache-v1"
)
RECEIPT_PATH = Path(
    "outputs/runtime-environment/receipts/"
    "official-public-source-retrieval-requalification-v6-20260801.json"
)
MATRIX_PATH = Path(
    "outputs/runtime-environment/"
    "runtime-readiness-matrix-source-binding-v7.json"
)
SUMMARY_PATH = Path(
    "outputs/runtime-environment/"
    "runtime-health-summary-source-binding-v7.json"
)
OBSERVED_AT = "2026-08-01T05:20:00Z"
CHANGED_SOURCE_IDS = (
    "lca.kernel.official-public-source-index",
)
HISTORICAL_ADAPTER_VERSION = "1.6.1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _git_object(root: Path, commit: str, relative: str) -> bytes:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def _source_entries(value: Mapping[str, object]) -> dict[str, dict]:
    rows = value.get("sources")
    if not isinstance(rows, list):
        raise ValueError("source lock rows are absent")
    indexed: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("source_id"), str):
            raise ValueError("source lock row is invalid")
        source_id = str(row["source_id"])
        if source_id in indexed:
            raise ValueError(f"duplicate source lock row: {source_id}")
        indexed[source_id] = deepcopy(dict(row))
    return indexed


def _tree_sha256(root: Path) -> str:
    rows: list[dict] = []
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            rows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _sha256(path),
                }
            )
    return hashlib.sha256(_canonical(rows)).hexdigest()


class _ForbiddenTransport:
    def request(self, *_: object, **__: object) -> dict:
        raise AssertionError("requalification must not perform a network read")


def build_requalification(root: Path) -> dict[str, dict]:
    root = Path(root).resolve()
    old_matrix_path = root / OLD_MATRIX
    if _sha256(old_matrix_path) != OLD_MATRIX_SHA256:
        raise ValueError("historical runtime matrix drifted")

    source_lock_relative = "lca_research_kernel/contracts/tiangong-source-lock.json"
    previous_bytes = _git_object(root, BASELINE_COMMIT, source_lock_relative)
    if hashlib.sha256(previous_bytes).hexdigest() != OLD_SOURCE_LOCK_SHA256:
        raise ValueError("historical source lock Git object drifted")
    previous_lock = json.loads(previous_bytes)
    current_lock_path = root / source_lock_relative
    current_lock = _read(current_lock_path)
    previous_entries = _source_entries(previous_lock)
    current_entries = _source_entries(current_lock)
    changed = sorted(
        source_id
        for source_id in set(previous_entries) | set(current_entries)
        if previous_entries.get(source_id) != current_entries.get(source_id)
    )
    if changed != list(CHANGED_SOURCE_IDS):
        raise ValueError(f"unexpected source-lock change closure: {changed}")

    cache_root = root / CACHE_ROOT
    before = _tree_sha256(cache_root)
    capability = OfficialPublicSourceCapability(
        cache_root,
        transport=_ForbiddenTransport(),
        wall_clock=lambda: OBSERVED_AT,
    )
    result = capability.execute(
        {
            "contract": "official_public_source_request/v1",
            "question_id": "q14-source-probe",
            "run_id": "q14-source-probe",
            "method": "GET",
            "url": "https://eur-lex.europa.eu/eli/reg_impl/2025/2547/oj/eng",
            "parse_location": "precursor",
        }
    )
    after = _tree_sha256(cache_root)
    content = result.get("content")
    if not isinstance(content, Mapping):
        raise ValueError("requalification source content is absent")
    source_receipt = content.get("source_receipt")
    source_binding = content.get("source_binding")
    if (
        not isinstance(source_receipt, Mapping)
        or source_receipt.get("cache_hit") is not True
        or source_receipt.get("network_reads_this_request") != 0
        or source_receipt.get("body_sha256")
        != "d8c2e8b2215df3f2608d8fabf0427ba49c7b1c28b17d7525974babcfb997a70b"
        or not validate_official_source_observation(source_binding)
        or before != after
    ):
        raise ValueError("official-source binding requalification failed")

    contract = RuntimeEnvironmentContract.load(root)
    receipt = {
        "contract": (
            "lca_research_kernel.official_source_binding_requalification/v1"
        ),
        "status": "passed",
        "observed_at": OBSERVED_AT,
        "baseline_commit": BASELINE_COMMIT,
        "historical_runtime_matrix": {
            "path": OLD_MATRIX.as_posix(),
            "sha256": OLD_MATRIX_SHA256,
        },
        "source_lock_transition": {
            "previous_sha256": OLD_SOURCE_LOCK_SHA256,
            "current_sha256": _sha256(current_lock_path),
            "changed_source_ids": changed,
            "unaffected_source_count": len(current_entries) - len(changed),
        },
        "implementation": {
            "adapter_source_id": "lca.kernel.official-public-source",
            "adapter_version": HISTORICAL_ADAPTER_VERSION,
            "binding_source_id": "lca.kernel.official-source-binding",
            "binding_version": "1.0.2",
            "source_index_version": "2026-08-01.6",
        },
        "real_cached_probe": {
            "cache_root": CACHE_ROOT.as_posix(),
            "cache_tree_sha256_before": before,
            "cache_tree_sha256_after": after,
            "source_receipt": deepcopy(dict(source_receipt)),
            "source_binding": deepcopy(dict(source_binding)),
        },
        "evidence_dag": {
            "invalidated_capability_ids": ["official-public-source-read"],
            "reused_unaffected_capability_count": (
                len(contract.capability_ids) - 1
            ),
            "rerun_route": "requalify_changed_adapter_only",
        },
        "model_calls": 0,
        "network_reads": 0,
        "external_writes": 0,
        "scientific_results_created": 0,
    }
    receipt["receipt_sha256"] = hashlib.sha256(_canonical(receipt)).hexdigest()

    old_matrix = _read(old_matrix_path)
    matrix = deepcopy(old_matrix)
    matrix["catalog_snapshot_sha256"] = contract.catalog_snapshot_sha256
    matrix["source_lock_file_sha256"] = contract.source_lock_file_sha256
    matrix["observed_at"] = OBSERVED_AT

    receipt_bytes = json.dumps(
        receipt, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"
    receipt_file_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
    matched = 0
    for row in matrix["capabilities"]:
        if row["capability_id"] == "official-public-source-read":
            matched += 1
            evidence = {
                "kind": "real_runtime_probe",
                "receipt_ref": RECEIPT_PATH.as_posix(),
                "receipt_sha256": receipt_file_sha256,
            }
            row["evidence"] = {
                gate: deepcopy(evidence) for gate in contract.executable_gates
            }
    if matched != 1:
        raise ValueError("official-source capability row is not unique")
    errors = contract.validate_matrix(matrix, require_closed=True)
    if errors:
        raise ValueError("requalified runtime matrix failed: " + "; ".join(errors))

    matrix_bytes = json.dumps(
        matrix, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"
    old_summary = _read(root / OLD_SUMMARY)
    summary = deepcopy(old_summary)
    summary["observed_at"] = OBSERVED_AT
    summary["matrix"] = {
        "path": MATRIX_PATH.as_posix(),
        "sha256": hashlib.sha256(matrix_bytes).hexdigest(),
    }
    summary["project_record"] = "outputs/runtime-environment/project-record-v8"
    summary["requalification"] = {
        "receipt_ref": RECEIPT_PATH.as_posix(),
        "receipt_sha256": receipt_file_sha256,
        "scope": "official-public-source-read",
        "unchanged_rows_reused": len(contract.capability_ids) - 1,
    }
    return {"receipt": receipt, "matrix": matrix, "summary": summary}


def write_requalification(root: Path) -> dict[str, str]:
    root = Path(root).resolve()
    documents = build_requalification(root)
    paths = {
        "receipt": root / RECEIPT_PATH,
        "matrix": root / MATRIX_PATH,
        "summary": root / SUMMARY_PATH,
    }
    result: dict[str, str] = {}
    for name, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(
                documents[name], ensure_ascii=False, sort_keys=True, indent=2
            )
            + "\n"
        ).encode("utf-8")
        path.write_bytes(payload)
        result[f"{name}_path"] = path.relative_to(root).as_posix()
        result[f"{name}_sha256"] = hashlib.sha256(payload).hexdigest()
    matrix = documents["matrix"]
    verify_matrix_receipts(root, matrix)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = (
        write_requalification(args.root)
        if args.write
        else {
            name: value.get("status", "valid")
            for name, value in build_requalification(args.root).items()
        }
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
