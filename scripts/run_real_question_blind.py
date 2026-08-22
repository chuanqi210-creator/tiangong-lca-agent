#!/usr/bin/env python3
"""Run question-only evaluation inputs through an injected Product Interface."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from lca_research_evaluation.real_question_blind_runner import (
    RealQuestionBlindRunner,
)
from lca_research_kernel.execution_environment import (
    ProjectExecutionEnvironment,
)
from lca_research_service.runtime_factory import RuntimeFactory


ROOT = Path(__file__).resolve().parents[1]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def run_with_product_interface(
    product_interface: object,
    *,
    question_root: Path,
    blind_manifest: Mapping[str, object],
    execution_environment: object,
    question_read_authorization_receipt: Mapping[str, object],
    output_root: Path,
    sealed_root: Path,
    sealed_identifiers: set[str],
    official_source_cache_root: Path | None,
    sealed_package_receipt_path: Path,
    accepted_package_receipt_sha256: str,
    extraction_ledger_path: Path,
    question_ids: Sequence[str] | None = None,
    clock: Callable[[], str] = _utc_now,
) -> dict:
    """Keep runtime construction outside the evaluation runner."""

    runner = RealQuestionBlindRunner(
        product_interface=product_interface,
        question_root=question_root,
        blind_manifest=blind_manifest,
        execution_environment=execution_environment,
        question_read_authorization_receipt=question_read_authorization_receipt,
        output_root=output_root,
        sealed_root=sealed_root,
        sealed_identifiers=sealed_identifiers,
        official_source_cache_root=official_source_cache_root,
        sealed_package_receipt_path=sealed_package_receipt_path,
        accepted_package_receipt_sha256=accepted_package_receipt_sha256,
        extraction_ledger_path=extraction_ledger_path,
        clock=clock,
    )
    return runner.run(question_ids=question_ids)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the frozen question-only real-question baseline."
    )
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--runtime-data-root", type=Path, required=True)
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--execution-research-id", required=True)
    parser.add_argument("--execution-policy", type=Path, required=True)
    parser.add_argument("--question-root", type=Path, required=True)
    parser.add_argument("--blind-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--sealed-root", type=Path, required=True)
    parser.add_argument(
        "--sealed-identifier",
        action="append",
        dest="sealed_identifiers",
        required=True,
    )
    parser.add_argument("--official-source-cache-root", type=Path, required=True)
    parser.add_argument("--sealed-package-receipt", type=Path, required=True)
    parser.add_argument("--accepted-package-receipt-sha256", required=True)
    parser.add_argument("--extraction-ledger", type=Path, required=True)
    parser.add_argument(
        "--question-id",
        action="append",
        dest="question_ids",
        help="Run one or more scored IDs in canonical order; omit for all 19.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    policy = _load_object(args.execution_policy, "execution policy")
    manifest = _load_object(args.blind_manifest, "blind-run manifest")
    environment = ProjectExecutionEnvironment.create(
        args.execution_root,
        args.execution_research_id,
        policy,
    )
    authorization = environment.authorize_file(
        args.question_root / "real-question-pack.json",
        "read",
    )
    binding = RuntimeFactory(
        repository_root=args.repository_root,
        data_root=args.runtime_data_root,
    ).build()
    if not binding.ready or binding.runtime is None:
        raise RuntimeError("Product Interface runtime is unavailable")
    product_interface = binding.runtime.product_interface
    report = run_with_product_interface(
        product_interface,
        question_root=args.question_root,
        blind_manifest=manifest,
        execution_environment=environment,
        question_read_authorization_receipt=authorization,
        output_root=args.output_root,
        sealed_root=args.sealed_root,
        sealed_identifiers=set(args.sealed_identifiers),
        official_source_cache_root=args.official_source_cache_root,
        sealed_package_receipt_path=args.sealed_package_receipt,
        accepted_package_receipt_sha256=(
            args.accepted_package_receipt_sha256
        ),
        extraction_ledger_path=args.extraction_ledger,
        question_ids=args.question_ids,
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
