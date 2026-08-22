#!/usr/bin/env python3
"""Review current model probes and compile a catalog promotion candidate."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def review(root: Path, output_root: Path) -> dict:
    root = Path(root).resolve()
    sys.path.insert(0, str(root))
    from lca_research_kernel.model_provider_admission import (
        review_provider_probe,
    )
    from lca_research_kernel.model_provider_catalog import admit_providers
    from lca_research_kernel.model_runtime import load_model_runtime_contract

    contract = load_model_runtime_contract(root)
    catalog = contract["provider_catalog"]
    admissions = {}
    summaries = {}
    for provider_id in catalog["current_validation_gate"]:
        receipt_path = (
            output_root / f"{provider_id}-structured-probe-receipt.json"
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        review_value = review_provider_probe(
            root,
            provider_id=provider_id,
            receipt=receipt,
            trace_path=output_root / f"{provider_id}-probe-run-trace.jsonl",
        )
        if review_value["status"] != "passed":
            raise ValueError(f"provider review failed: {provider_id}")
        _write(
            output_root / f"{provider_id}-admission-review-v1.json",
            review_value,
        )
        admissions[provider_id] = {
            "receipt": receipt,
            "independent_review": review_value,
        }
        summaries[provider_id] = {
            "receipt_sha256": receipt["receipt_sha256"],
            "review_sha256": review_value["review_sha256"],
            "status": "accepted_for_catalog_admission",
        }
    promoted = admit_providers(catalog, admissions)
    _write(output_root / "model-provider-catalog-promoted-candidate.json", promoted)
    result = {
        "contract": "lca_research_kernel.model_provider_admission_batch/v1",
        "status": "passed",
        "providers": summaries,
        "nonblocking_provider_states": {
            row["provider_id"]: row["admission_state"]
            for row in promoted["providers"]
            if row["provider_id"] not in catalog["current_validation_gate"]
        },
        "external_effects": 0,
    }
    _write(output_root / "provider-admission-batch-v1.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("outputs/d18-production-runtime-core/final"),
    )
    args = parser.parse_args()
    output = args.output_root
    if not output.is_absolute():
        output = args.root.resolve() / output
    result = review(args.root, output)
    print(
        json.dumps(
            {
                "status": result["status"],
                "providers": sorted(result["providers"]),
                "external_effects": result["external_effects"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
