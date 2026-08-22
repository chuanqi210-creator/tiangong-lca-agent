"""Grade an explicitly frozen development-regression subset."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Sequence

from .dual_track_grader import (
    _canonical_sha256,
    _normalize_assessment,
    _validate_sealed_package_basis,
)
from .real_question_contracts import HARD_VETOES, PDF_SHA256
from .real_question_evidence_invalidation import (
    verify_development_regression_raw_lock,
)


PER_QUESTION_QUALITY_FLOOR = Decimal(80)
AGGREGATE_QUALITY_FLOOR = Decimal(90)


def build_development_regression_grade(
    assessments: Sequence[Mapping[str, object]],
    *,
    raw_root: Path,
    raw_lock: Mapping[str, object],
    sealed_reference_path: Path,
    sealed_package_receipt_path: Path,
    accepted_package_receipt_sha256: str,
    extraction_ledger_path: Path,
) -> dict:
    """Reveal references only after the named subset is byte-verified."""

    raw_root = Path(raw_root)
    verification = verify_development_regression_raw_lock(raw_root, raw_lock)
    if verification.get("status") != "valid":
        raise ValueError("development-regression raw evidence is not frozen")
    runs = verification["runs"]
    question_ids = verification["expected_question_ids"]
    if (
        not isinstance(assessments, (list, tuple))
        or [str(row.get("question_id")) for row in assessments] != question_ids
    ):
        raise ValueError("development-regression assessment population drifted")
    (
        sealed_reference_sha256,
        sealed_identities,
        package_receipt_sha256,
        extraction_ledger_sha256,
    ) = _validate_sealed_package_basis(
        sealed_reference_path=Path(sealed_reference_path),
        sealed_package_receipt_path=Path(sealed_package_receipt_path),
        accepted_package_receipt_sha256=accepted_package_receipt_sha256,
        extraction_ledger_path=Path(extraction_ledger_path),
    )

    evidence_rows = []
    failure_by_question = {}
    for assessment, run in zip(assessments, runs):
        question_id = str(assessment.get("question_id"))
        normalized, failures = _normalize_assessment(
            assessment,
            raw_root=raw_root.resolve(),
            run=run,
            sealed_identity=sealed_identities[question_id],
            task_adaptive=True,
        )
        evidence_rows.append(normalized)
        if failures:
            failure_by_question[question_id] = list(failures)

    average = sum(
        (Decimal(str(row["quality_score"])) for row in evidence_rows),
        Decimal(0),
    ) / Decimal(len(evidence_rows))
    if average < AGGREGATE_QUALITY_FLOOR:
        for row in evidence_rows:
            failure_by_question.setdefault(row["question_id"], []).append(
                "aggregate_quality_below_floor"
            )
    aggregate_vetoes = {
        veto: all(row["hard_vetoes"][veto] for row in evidence_rows)
        for veto in HARD_VETOES
    }
    evidence = {
        "contract": "development_regression_dual_track_grade_evidence/v1",
        "pdf_sha256": PDF_SHA256,
        "question_ids": deepcopy(question_ids),
        "execution_lock_sha256": verification["execution_lock_sha256"],
        "raw_evidence_sha256": verification["raw_evidence_sha256"],
        "raw_lock_verification_sha256": verification["verification_sha256"],
        "sealed_reference_sha256": sealed_reference_sha256,
        "sealed_package_receipt_sha256": package_receipt_sha256,
        "extraction_ledger_sha256": extraction_ledger_sha256,
        "correctness_authority": "official_evidence",
        "reference_answer_role": "secondary_expert_comparison",
        "question_evidence": evidence_rows,
    }
    evidence["evidence_sha256"] = _canonical_sha256(evidence)
    package = {
        "contract": "development_regression_dual_track_grade_package/v1",
        "status": "failed" if failure_by_question else "passed",
        "grade": None,
        "evidence": evidence,
        "failure_observation": None,
    }
    if failure_by_question:
        observation = {
            "contract": "development_regression_grade_failure_observation/v1",
            "failed_question_ids": [
                question_id
                for question_id in question_ids
                if question_id in failure_by_question
            ],
            "failures": failure_by_question,
            "thresholds_changed": False,
            "raw_evidence_rewritten": False,
            "cross_epoch_splicing": False,
        }
        observation["observation_sha256"] = _canonical_sha256(observation)
        package["failure_observation"] = observation
    else:
        package["grade"] = {
            "contract": "development_regression_dual_track_grade/v1",
            "pdf_sha256": PDF_SHA256,
            "question_ids": deepcopy(question_ids),
            "question_count": len(question_ids),
            "correctness_authority": "official_evidence",
            "reference_answer_role": "secondary_expert_comparison",
            "thresholds": {"aggregate": 90, "per_question": 80},
            "question_grades": [
                {
                    "question_id": row["question_id"],
                    "official_exact_accuracy": row["official_exact_accuracy"],
                    "quality_score": row["quality_score"],
                    "reference_coverage_score": row[
                        "reference_coverage_score"
                    ],
                }
                for row in evidence_rows
            ],
            "average_quality_score": (
                float(average) if average % 1 else int(average)
            ),
            "hard_vetoes": aggregate_vetoes,
            "all_vetoes_passed": all(aggregate_vetoes.values()),
            "claim_scope": "development_regression_only",
        }
    package["package_sha256"] = _canonical_sha256(package)
    return package


__all__ = ["build_development_regression_grade"]
