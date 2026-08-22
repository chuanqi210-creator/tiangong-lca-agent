#!/usr/bin/env python3
"""Requalify runtime readiness after a source-index-only lock update."""

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

from lca_research_kernel.runtime_environment import RuntimeEnvironmentContract
from lca_research_kernel.runtime_environment_health import verify_matrix_receipts


BASE_COMMIT = "1c417e6683237c13dea9b1bf0df6b94eae1510c6"
BASE_MATRIX = Path(
    "outputs/runtime-environment/runtime-readiness-matrix-source-binding-v7.json"
)
BASE_MATRIX_SHA256 = (
    "09b79e7f3f6d89f843f52acc195195fe1314529a34d0547fb649d03913243ea2"
)
BASE_SUMMARY = Path(
    "outputs/runtime-environment/runtime-health-summary-source-binding-v7.json"
)
CURRENT_MATRIX = Path(
    "outputs/runtime-environment/runtime-readiness-matrix-source-binding-v8.json"
)
CURRENT_SUMMARY = Path(
    "outputs/runtime-environment/runtime-health-summary-source-binding-v8.json"
)
RECEIPT = Path(
    "outputs/runtime-environment/receipts/"
    "runtime-source-index-requalification-v8-20260803.json"
)
OBSERVED_AT = "2026-08-03T00:05:00+08:00"
SOURCE_LOCK_PATH = Path(
    "lca_research_kernel/contracts/tiangong-source-lock.json"
)
CATALOG_PATH = Path(
    "lca_research_kernel/contracts/tiangong-capability-catalog.json"
)
UNCHANGED_RUNTIME_PATHS = (
    "lca_research_kernel/official_public_source.py",
    "lca_research_kernel/official_source_binding.py",
    CATALOG_PATH.as_posix(),
)
EXPECTED_CHANGED_SOURCE_IDS = {"lca.kernel.official-public-source-index"}
EXPECTED_CHANGED_SOURCE_FIELDS = {"integrity", "version"}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _read(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _git_bytes(root: Path, relative: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{BASE_COMMIT}:{relative}"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def _source_rows(value: Mapping[str, object]) -> dict[str, dict]:
    sources = value.get("sources")
    if not isinstance(sources, list):
        raise ValueError("source lock rows are absent")
    rows = {}
    for source in sources:
        if not isinstance(source, Mapping) or not isinstance(
            source.get("source_id"), str
        ):
            raise ValueError("source lock row is invalid")
        source_id = source["source_id"]
        if source_id in rows:
            raise ValueError(f"duplicate source lock row: {source_id}")
        rows[source_id] = deepcopy(dict(source))
    return rows


def validate_source_index_row_change(
    prior_row: Mapping[str, object], current_row: Mapping[str, object]
) -> list[str]:
    """Allow only the content version and its digest to change."""

    changed_fields = sorted(
        field
        for field in set(prior_row) | set(current_row)
        if prior_row.get(field) != current_row.get(field)
    )
    if set(changed_fields) != EXPECTED_CHANGED_SOURCE_FIELDS:
        raise ValueError(
            "source-index row change exceeds version/integrity metadata: "
            + ", ".join(changed_fields)
        )
    return changed_fields


def build_requalification(root: Path) -> dict[str, dict]:
    root = Path(root).resolve()
    base_matrix_path = root / BASE_MATRIX
    if _sha256(base_matrix_path) != BASE_MATRIX_SHA256:
        raise ValueError("accepted v7 runtime matrix drifted")

    unchanged_git_objects = []
    for relative in UNCHANGED_RUNTIME_PATHS:
        historical = _git_bytes(root, relative)
        current = (root / relative).read_bytes()
        if current != historical:
            raise ValueError(f"runtime implementation changed: {relative}")
        unchanged_git_objects.append(
            {
                "commit": BASE_COMMIT,
                "path": relative,
                "sha256": _sha256_bytes(historical),
                "bytes": len(historical),
            }
        )

    prior_lock_bytes = _git_bytes(root, SOURCE_LOCK_PATH.as_posix())
    prior_lock = json.loads(prior_lock_bytes)
    current_lock_path = root / SOURCE_LOCK_PATH
    current_lock = _read(current_lock_path)
    prior_rows = _source_rows(prior_lock)
    current_rows = _source_rows(current_lock)
    changed_source_ids = {
        source_id
        for source_id in set(prior_rows) | set(current_rows)
        if prior_rows.get(source_id) != current_rows.get(source_id)
    }
    if changed_source_ids != EXPECTED_CHANGED_SOURCE_IDS:
        raise ValueError(
            "source-lock change exceeds the accepted metadata scope: "
            + ", ".join(sorted(changed_source_ids))
        )
    changed_source_fields = validate_source_index_row_change(
        prior_rows["lca.kernel.official-public-source-index"],
        current_rows["lca.kernel.official-public-source-index"],
    )

    contract = RuntimeEnvironmentContract.load(root)
    matrix = deepcopy(_read(base_matrix_path))
    matrix["catalog_snapshot_sha256"] = contract.catalog_snapshot_sha256
    matrix["source_lock_file_sha256"] = contract.source_lock_file_sha256
    matrix["observed_at"] = OBSERVED_AT
    errors = contract.validate_matrix(matrix, require_closed=True)
    if errors:
        raise ValueError("requalified matrix failed: " + "; ".join(errors))
    verify_matrix_receipts(root, matrix)

    receipt = {
        "contract": (
            "lca_research_kernel.runtime_source_index_requalification/v1"
        ),
        "status": "passed",
        "observed_at": OBSERVED_AT,
        "baseline": {
            "commit": BASE_COMMIT,
            "matrix": {
                "path": BASE_MATRIX.as_posix(),
                "sha256": BASE_MATRIX_SHA256,
            },
            "source_lock_sha256": _sha256_bytes(prior_lock_bytes),
        },
        "current": {
            "catalog_snapshot_sha256": contract.catalog_snapshot_sha256,
            "source_lock_sha256": _sha256(current_lock_path),
            "changed_source_ids": sorted(changed_source_ids),
            "changed_source_fields": changed_source_fields,
            "unchanged_source_count": len(current_rows) - len(changed_source_ids),
        },
        "unchanged_runtime_git_objects": unchanged_git_objects,
        "impact_classification": (
            "static_source_index_metadata_changed_runtime_adapter_unchanged"
        ),
        "matrix_validation": "passed",
        "reused_real_probe_receipts": len(matrix["capabilities"]),
        "model_calls": 0,
        "network_reads": 0,
        "external_writes": 0,
        "research_cases": 0,
        "lca_calculations": 0,
    }
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    receipt_bytes = (
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")

    matrix_bytes = (
        json.dumps(matrix, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    summary = deepcopy(_read(root / BASE_SUMMARY))
    summary["observed_at"] = OBSERVED_AT
    summary["matrix"] = {
        "path": CURRENT_MATRIX.as_posix(),
        "sha256": _sha256_bytes(matrix_bytes),
    }
    summary["project_record"] = "outputs/runtime-environment/project-record-v10"
    summary["requalification"] = {
        "receipt_ref": RECEIPT.as_posix(),
        "receipt_sha256": _sha256_bytes(receipt_bytes),
        "scope": "official-public-source-index-metadata",
        "unchanged_rows_reused": len(contract.capability_ids),
    }
    return {"receipt": receipt, "matrix": matrix, "summary": summary}


def write_requalification(root: Path) -> dict:
    root = Path(root).resolve()
    values = build_requalification(root)
    paths = {
        "receipt": root / RECEIPT,
        "matrix": root / CURRENT_MATRIX,
        "summary": root / CURRENT_SUMMARY,
    }
    result = {}
    for name, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(values[name], ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        ).encode("utf-8")
        path.write_bytes(payload)
        result[f"{name}_path"] = path.relative_to(root).as_posix()
        result[f"{name}_sha256"] = _sha256_bytes(payload)
    verify_matrix_receipts(root, values["matrix"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = (
        write_requalification(args.root)
        if args.write
        else {key: "valid" for key in build_requalification(args.root)}
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
