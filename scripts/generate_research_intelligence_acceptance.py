#!/usr/bin/env python3
"""Generate the redacted D-18 research-intelligence Beat A receipt."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.research_intelligence_eval import (
    build_acceptance_receipt,
    grade_research_dialogue,
    run_research_dialogue,
)
from lca_research_kernel.assurance import AssuranceBoundary
from lca_research_kernel.model_gateway import ModelGateway
from lca_research_kernel.product_interface import ProductInterface
from tests.test_product_material_acceptance import (
    ProductMaterialAcceptanceTests,
    material_bundle,
)
from tests.test_research_intelligence_eval import (
    FIXTURE_PATH,
    NOW,
    QualityDialogueAdapter,
    ResearchIntelligenceEvaluationTests,
    _replace_bundle_evidence,
)


BASELINE_COMMIT = "6e14918260cb860e395e8b6df737d7df08de7b62"
NEXT_ROUTE = "render-exact-material-citations-and-source-use"
COUNTERFACTUAL_TESTS = {
    "rename_invariance": (
        "test_file_and_flow_rename_preserve_state_driven_answer_plan"
    ),
    "missing_required_source_value": (
        "test_missing_required_source_value_returns_named_bounded_dependency"
    ),
    "allowed_unselected_contradiction": (
        "test_allowed_but_unselected_contradictory_row_"
        "never_reaches_model_or_citation"
    ),
}


def _run_counterfactuals() -> dict[str, str]:
    suite = unittest.TestSuite(
        ResearchIntelligenceEvaluationTests(name)
        for name in COUNTERFACTUAL_TESTS.values()
    )
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("research-intelligence counterfactuals failed")
    return {name: "passed" for name in COUNTERFACTUAL_TESTS}


def _candidate_run() -> tuple[dict, dict]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    bundle = material_bundle()
    bundle["research_material"]["scope"] = "project"
    bundle = _replace_bundle_evidence(
        bundle,
        content="flow=water; amount=2; unit=kg; check==1+1",
        semantic_roles=["flow", "amount", "unit", "validation_expression"],
        artifact_sha="a" * 64,
    )
    adapter = QualityDialogueAdapter(fixture)
    with tempfile.TemporaryDirectory() as temporary:
        product = ProductInterface(
            Path(temporary),
            model_gateway=ModelGateway(
                {"fake": adapter}, default_provider="fake"
            ),
            assurance=AssuranceBoundary(),
            clock=lambda: NOW,
            run_id="research-intelligence-acceptance",
            verified_content_resolver=(
                ProductMaterialAcceptanceTests._material_resolver([bundle])
            ),
        )
        run = run_research_dialogue(product, fixture, bundle)
        grade = grade_research_dialogue(
            run,
            project_record=product._open_record(fixture["project"]["research_id"]),
        )
    return run, grade


def _atomic_write(path: Path, value: dict) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".receipt-")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    run, grade = _candidate_run()
    receipt = build_acceptance_receipt(
        run,
        grade,
        baseline_commit=BASELINE_COMMIT,
        candidate_commit=candidate_commit,
        counterfactuals=_run_counterfactuals(),
        next_route=NEXT_ROUTE,
    )
    _atomic_write(args.output, receipt)
    print(receipt["receipt_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
