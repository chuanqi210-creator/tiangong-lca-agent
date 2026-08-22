"""Fail-closed contracts for the sealed real-question optimization program."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Mapping


MACHINE_CONTRACT = "real_question_optimization_contract/v1"
MACHINE_CONTRACT_PATH = Path("docs/real-question-optimization-contract.json")
ARTIFACT_CONTRACTS = [
    "real_question_pack/v1",
    "sealed_reference_answer/v1",
    "blind_run_manifest/v1",
    "dual_track_grade/v1",
    "optimization_decision/v1",
]
PDF_SHA256 = "73101eec6999b27f4409c6e90f1a41343af694cb41414af6c55fb64482d6775c"
QUESTION_IDS = [f"Q{number}" for number in range(1, 21)]
SCORED_QUESTION_IDS = [
    question_id for question_id in QUESTION_IDS if question_id != "Q2"
]
QUESTION_DISPOSITIONS = {
    question_id: (
        "contaminated_calibration_only"
        if question_id == "Q2"
        else "scored_blind_baseline"
    )
    for question_id in QUESTION_IDS
}
FAILURE_LAYERS = [
    "prompt_failure",
    "context_failure",
    "harness_failure",
    "loop_failure",
    "model_limitation",
]
INVALIDATION_ROUTES = [
    "rerun_full_execution",
    "regrade_existing_traces",
    "rerun_clean_acceptance",
    "rebuild_report_only",
    "no_evidence_invalidation",
    "unknown_change_fail_closed",
]
QUESTION_DISPOSITION_VALUES = [
    "contaminated_calibration_only",
    "scored_blind_baseline",
]
EXACT_DIMENSIONS = [
    "numeric",
    "unit",
    "cn",
    "country",
    "year",
    "regulatory_time",
]
HARD_VETOES = [
    "answer_leakage",
    "official_numeric_binding_error",
    "critical_unit_or_regulatory_time_error",
    "deterministic_conclusion_despite_missing_critical_data",
    "secret_or_credential_exposure",
    "external_write",
    "case_name_driven_product_routing",
    "cross_question_evidence_splicing",
    "missing_official_evidence",
    "unsupported_strong_claim",
]
BLIND_RUN_FORBIDDEN_FIELDS = [
    "answer_body",
    "answer_path",
    "answer_sha256",
    "answers",
    "reference_answer",
    "sealed_reference",
]
FINAL_NEXT_ROUTE = "run-next-sealed-real-question-evaluation"
AVERAGE_QUALITY_TOLERANCE_TEXT = "0.000000000001"
AVERAGE_QUALITY_TOLERANCE = Decimal(AVERAGE_QUALITY_TOLERANCE_TEXT)
GENERIC_FIX_IDS = [
    "startup-idempotency",
    "provenance-separation",
    "budget-aware-provider-failover",
    "locked-verify-runtime",
    "official-source-composite-binding",
    "exception-scan",
    "claim-strength-classification",
    "answer-timing-budget",
    "answer-isolation",
    "evidence-invalidation",
]


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _expect_keys(value: object, expected: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{label} fields are invalid or unclassified")
    return value


def _expect_sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{64}", value) is None
    ):
        raise ValueError(f"{label} is not a lowercase SHA-256")
    return value


def _expect_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{label} is not a stable relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{label} is not a stable relative path")
    return value


def _expect_exact_question_ids(value: object, expected: list[str], label: str) -> None:
    if not isinstance(value, list) or value != expected:
        raise ValueError(f"{label} question population drifted")


def _expect_dispositions(value: object, label: str) -> None:
    dispositions = _expect_keys(value, set(QUESTION_IDS), label)
    if dict(dispositions) != QUESTION_DISPOSITIONS:
        raise ValueError(f"{label} must isolate Q2 and score exactly 19 questions")


def validate_real_question_optimization_contract(
    contract: Mapping[str, object],
) -> dict:
    """Validate the immutable program contract and its self-binding."""

    value = _expect_keys(
        contract,
        {
            "artifact_contracts",
            "authority",
            "baseline",
            "contract",
            "contract_sha256",
            "enums",
            "final_next_route",
            "generic_fix_ids",
            "isolation",
            "pdf",
            "thresholds",
            "unclassified_value_policy",
            "unknown_field_policy",
        },
        "real-question optimization contract",
    )
    if value["contract"] != MACHINE_CONTRACT:
        raise ValueError("real-question optimization contract identity drifted")
    if value["artifact_contracts"] != ARTIFACT_CONTRACTS:
        raise ValueError("versioned artifact contract registry drifted")

    pdf = _expect_keys(value["pdf"], {"question_ids", "sha256"}, "PDF binding")
    if pdf["sha256"] != PDF_SHA256:
        raise ValueError("real-question PDF binding drifted")
    _expect_exact_question_ids(pdf["question_ids"], QUESTION_IDS, "PDF")

    baseline = _expect_keys(
        value["baseline"],
        {"question_dispositions", "scored_question_count", "scored_question_ids"},
        "blind baseline",
    )
    _expect_dispositions(baseline["question_dispositions"], "blind baseline")
    if baseline["scored_question_count"] != 19:
        raise ValueError("blind baseline must score exactly 19 questions")
    _expect_exact_question_ids(
        baseline["scored_question_ids"], SCORED_QUESTION_IDS, "blind baseline"
    )

    authority = _expect_keys(
        value["authority"], {"correctness", "reference_answer"}, "grade authority"
    )
    if dict(authority) != {
        "correctness": "official_evidence",
        "reference_answer": "secondary_expert_comparison",
    }:
        raise ValueError("grade authority drifted")

    isolation = _expect_keys(
        value["isolation"],
        {
            "blind_run_forbidden_fields",
            "blind_run_visibility",
            "sealed_reference_visibility",
        },
        "answer isolation",
    )
    if dict(isolation) != {
        "blind_run_forbidden_fields": BLIND_RUN_FORBIDDEN_FIELDS,
        "blind_run_visibility": "question_only",
        "sealed_reference_visibility": "grader_only",
    }:
        raise ValueError("answer isolation contract drifted")

    thresholds = _expect_keys(
        value["thresholds"],
        {
            "all_vetoes_must_pass",
            "average_quality_tolerance",
            "average_quality_min",
            "exact_accuracy",
            "exact_dimensions",
            "hard_vetoes",
            "per_question_quality_min",
            "veto_tolerance",
        },
        "grade thresholds",
    )
    if dict(thresholds) != {
        "all_vetoes_must_pass": True,
        "average_quality_tolerance": AVERAGE_QUALITY_TOLERANCE_TEXT,
        "average_quality_min": 90,
        "exact_accuracy": 1.0,
        "exact_dimensions": EXACT_DIMENSIONS,
        "hard_vetoes": HARD_VETOES,
        "per_question_quality_min": 80,
        "veto_tolerance": 0,
    }:
        raise ValueError("grade thresholds drifted")

    enums = _expect_keys(
        value["enums"],
        {"failure_layers", "invalidation_routes", "question_dispositions"},
        "real-question enums",
    )
    if dict(enums) != {
        "failure_layers": FAILURE_LAYERS,
        "invalidation_routes": INVALIDATION_ROUTES,
        "question_dispositions": QUESTION_DISPOSITION_VALUES,
    }:
        raise ValueError("real-question enum registry drifted")
    if value["generic_fix_ids"] != GENERIC_FIX_IDS:
        raise ValueError("generic fix ID registry drifted")
    if value["final_next_route"] != FINAL_NEXT_ROUTE:
        raise ValueError("final next route drifted")
    if value["unknown_field_policy"] != "reject":
        raise ValueError("unknown fields must be rejected")
    if value["unclassified_value_policy"] != "fail_closed":
        raise ValueError("unclassified values must fail closed")

    stored_hash = _expect_sha256(
        value["contract_sha256"], "real-question optimization contract self hash"
    )
    body = dict(value)
    body.pop("contract_sha256")
    if _canonical_sha256(body) != stored_hash:
        raise ValueError("real-question optimization contract self hash drifted")
    return dict(value)


def load_real_question_optimization_contract(
    root: Path,
    path: Path | None = None,
) -> dict:
    root = Path(root).resolve()
    contract_path = Path(path) if path is not None else root / MACHINE_CONTRACT_PATH
    if not contract_path.is_absolute():
        contract_path = root / contract_path
    try:
        value = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("real-question optimization contract is unreadable") from error
    if not isinstance(value, Mapping):
        raise ValueError("real-question optimization contract must be an object")
    return validate_real_question_optimization_contract(value)


def _validate_pack(value: Mapping[str, object]) -> None:
    _expect_keys(
        value,
        {"contract", "pdf_sha256", "questions", "scored_question_ids"},
        "real-question pack",
    )
    if value["pdf_sha256"] != PDF_SHA256:
        raise ValueError("real-question pack PDF binding drifted")
    questions = value["questions"]
    if not isinstance(questions, list) or len(questions) != 20:
        raise ValueError("real-question pack must contain exactly 20 questions")
    observed_ids = []
    for row in questions:
        question = _expect_keys(
            row,
            {
                "question_id",
                "disposition",
                "question_body",
                "page_number",
                "image_refs",
            },
            "real-question row",
        )
        question_id = question["question_id"]
        observed_ids.append(question_id)
        if (
            question_id not in QUESTION_DISPOSITIONS
            or question["disposition"] != QUESTION_DISPOSITIONS[question_id]
        ):
            raise ValueError("real-question disposition is invalid")
        if not isinstance(question["question_body"], str) or not question["question_body"].strip():
            raise ValueError("real-question body is absent")
        if (
            isinstance(question["page_number"], bool)
            or not isinstance(question["page_number"], int)
            or question["page_number"] < 1
        ):
            raise ValueError("real-question PDF page is invalid")
        refs = question["image_refs"]
        if not isinstance(refs, list):
            raise ValueError("real-question image binding is invalid")
        for ref in refs:
            binding = _expect_keys(ref, {"path", "sha256"}, "question image binding")
            _expect_relative_path(binding["path"], "question image path")
            _expect_sha256(binding["sha256"], "question image hash")
    _expect_exact_question_ids(observed_ids, QUESTION_IDS, "real-question pack")
    _expect_exact_question_ids(
        value["scored_question_ids"], SCORED_QUESTION_IDS, "real-question pack scored"
    )


def _validate_sealed_answers(value: Mapping[str, object]) -> None:
    _expect_keys(
        value,
        {"contract", "pdf_sha256", "visibility", "answers"},
        "sealed reference answers",
    )
    if value["pdf_sha256"] != PDF_SHA256:
        raise ValueError("sealed reference PDF binding drifted")
    if value["visibility"] != "grader_only":
        raise ValueError("sealed reference answers must be grader-only")
    answers = value["answers"]
    if not isinstance(answers, list) or len(answers) != 20:
        raise ValueError("sealed reference answers must cover exactly 20 questions")
    observed_ids = []
    for row in answers:
        answer = _expect_keys(
            row,
            {
                "question_id",
                "answer_body",
                "answer_path",
                "answer_sha256",
                "page_numbers",
                "reference_claims",
            },
            "sealed reference answer row",
        )
        observed_ids.append(answer["question_id"])
        body = answer["answer_body"]
        if not isinstance(body, str) or not body.strip():
            raise ValueError("sealed reference answer body is absent")
        _expect_relative_path(answer["answer_path"], "sealed reference answer path")
        if answer["answer_sha256"] != hashlib.sha256(body.encode("utf-8")).hexdigest():
            raise ValueError("sealed reference answer body binding drifted")
        page_numbers = answer["page_numbers"]
        if (
            not isinstance(page_numbers, list)
            or not page_numbers
            or any(
                isinstance(page, bool) or not isinstance(page, int) or page < 1
                for page in page_numbers
            )
        ):
            raise ValueError("sealed reference answer page binding is invalid")
        claims = answer["reference_claims"]
        if not isinstance(claims, list) or any(
            not isinstance(claim, str) or not claim.strip() for claim in claims
        ):
            raise ValueError("sealed reference claims are invalid")
    _expect_exact_question_ids(observed_ids, QUESTION_IDS, "sealed reference answers")


def _validate_blind_manifest(value: Mapping[str, object]) -> None:
    _expect_keys(
        value,
        {
            "contract",
            "pdf_sha256",
            "question_ids",
            "question_dispositions",
            "question_pack_sha256",
            "question_tree_sha256",
            "code_sha256",
            "model_id",
            "model_settings_sha256",
            "permission_policy_sha256",
            "budget_sha256",
            "official_source_snapshot_sha256",
            "execution_identity_sha256",
            "question_read_authorization_sha256",
            "sealed_reference_access",
        },
        "blind run manifest",
    )
    if value["pdf_sha256"] != PDF_SHA256:
        raise ValueError("blind run PDF binding drifted")
    _expect_exact_question_ids(
        value["question_ids"], SCORED_QUESTION_IDS, "blind run manifest"
    )
    _expect_dispositions(value["question_dispositions"], "blind run manifest")
    for field in (
        "question_pack_sha256",
        "question_tree_sha256",
        "code_sha256",
        "model_settings_sha256",
        "permission_policy_sha256",
        "budget_sha256",
        "official_source_snapshot_sha256",
        "execution_identity_sha256",
        "question_read_authorization_sha256",
    ):
        _expect_sha256(value[field], f"blind run {field}")
    if not isinstance(value["model_id"], str) or not value["model_id"].strip():
        raise ValueError("blind run model identity is absent")
    if value["sealed_reference_access"] != "forbidden":
        raise ValueError("blind execution cannot access sealed reference answers")


def _score(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} is not numeric")
    score = Decimal(str(value))
    if not score.is_finite() or score < 0 or score > 100:
        raise ValueError(f"{label} is outside 0..100")
    return score


def _validate_grade(value: Mapping[str, object]) -> None:
    _expect_keys(
        value,
        {
            "contract",
            "pdf_sha256",
            "correctness_authority",
            "reference_answer_role",
            "question_grades",
            "average_quality_score",
            "hard_vetoes",
            "all_vetoes_passed",
        },
        "dual-track grade",
    )
    if value["pdf_sha256"] != PDF_SHA256:
        raise ValueError("dual-track grade PDF binding drifted")
    if value["correctness_authority"] != "official_evidence":
        raise ValueError("official evidence must remain correctness authority")
    if value["reference_answer_role"] != "secondary_expert_comparison":
        raise ValueError("reference answers must remain a secondary comparison")
    rows = value["question_grades"]
    if not isinstance(rows, list) or len(rows) != 19:
        raise ValueError("dual-track grade must contain exactly 19 scored questions")
    observed_ids = []
    quality_scores = []
    for row in rows:
        grade = _expect_keys(
            row,
            {
                "question_id",
                "official_exact_accuracy",
                "quality_score",
                "reference_coverage_score",
            },
            "dual-track question grade",
        )
        observed_ids.append(grade["question_id"])
        exact = _expect_keys(
            grade["official_exact_accuracy"],
            set(EXACT_DIMENSIONS),
            "official exact-accuracy dimensions",
        )
        if any(exact[dimension] is not True for dimension in EXACT_DIMENSIONS):
            raise ValueError("official numeric/unit/CN/country/year/time accuracy must be exact")
        quality = _score(grade["quality_score"], "question quality score")
        if quality < 80:
            raise ValueError("each scored question must have quality >= 80")
        quality_scores.append(quality)
        _score(grade["reference_coverage_score"], "reference coverage score")
    _expect_exact_question_ids(observed_ids, SCORED_QUESTION_IDS, "dual-track grade")
    reported_average = _score(
        value["average_quality_score"], "average quality score"
    )
    calculated_average = sum(quality_scores, Decimal(0)) / Decimal(len(quality_scores))
    if (
        abs(reported_average - calculated_average) > AVERAGE_QUALITY_TOLERANCE
        or reported_average < 90
        or calculated_average < 90
    ):
        raise ValueError("average quality must be within declared tolerance and >= 90")
    vetoes = _expect_keys(value["hard_vetoes"], set(HARD_VETOES), "hard vetoes")
    if any(vetoes[veto] is not True for veto in HARD_VETOES):
        raise ValueError("every hard veto must pass")
    if value["all_vetoes_passed"] is not True:
        raise ValueError("hard-veto aggregate must pass")


def _validate_optimization_decision(value: Mapping[str, object]) -> None:
    _expect_keys(
        value,
        {
            "contract",
            "generic_fix_id",
            "failure_layer",
            "invalidation_route",
            "next_route",
        },
        "optimization decision",
    )
    fix_id = value["generic_fix_id"]
    if fix_id not in GENERIC_FIX_IDS:
        raise ValueError("optimization decision must name one generic fix")
    if value["failure_layer"] not in FAILURE_LAYERS:
        raise ValueError("optimization failure layer is unclassified")
    if value["invalidation_route"] not in INVALIDATION_ROUTES:
        raise ValueError("optimization invalidation route is unclassified")
    if value["next_route"] != FINAL_NEXT_ROUTE:
        raise ValueError("optimization decision next route drifted")


_VALIDATORS = {
    "real_question_pack/v1": _validate_pack,
    "sealed_reference_answer/v1": _validate_sealed_answers,
    "blind_run_manifest/v1": _validate_blind_manifest,
    "dual_track_grade/v1": _validate_grade,
    "optimization_decision/v1": _validate_optimization_decision,
}


def validate_real_question_artifact(
    artifact: Mapping[str, object],
    machine_contract: Mapping[str, object],
) -> dict:
    """Validate one supported artifact; all unknown fields and values fail closed."""

    validate_real_question_optimization_contract(machine_contract)
    if not isinstance(artifact, Mapping):
        raise ValueError("real-question artifact must be an object")
    artifact_contract = artifact.get("contract")
    validator = _VALIDATORS.get(artifact_contract)
    if validator is None:
        raise ValueError("real-question artifact contract is unsupported")
    validator(artifact)
    return dict(artifact)


__all__ = [
    "ARTIFACT_CONTRACTS",
    "FINAL_NEXT_ROUTE",
    "MACHINE_CONTRACT",
    "MACHINE_CONTRACT_PATH",
    "PDF_SHA256",
    "QUESTION_IDS",
    "SCORED_QUESTION_IDS",
    "load_real_question_optimization_contract",
    "validate_real_question_artifact",
    "validate_real_question_optimization_contract",
]
