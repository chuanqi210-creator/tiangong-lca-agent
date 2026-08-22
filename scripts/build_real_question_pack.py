#!/usr/bin/env python3
"""Build isolated question and grader packages from one verified PDF extraction."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.real_question_contracts import PDF_SHA256
from lca_research_evaluation.real_question_pack import (
    build_isolated_real_question_packages,
    build_packaged_extraction_ledger,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _load_list(path: Path, label: str) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable") from error
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must contain a non-empty list")
    if any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{label} rows must be objects")
    return value


def validate_extraction_ledger_inputs(
    ledger: object,
    *,
    questions_path: Path,
    sealed_reference_path: Path,
) -> dict:
    """Bind both extracted inputs to the independent extraction ledger."""

    if (
        not isinstance(ledger, dict)
        or ledger.get("schema") != "pdf_question_answer_extraction_ledger/v1"
        or not isinstance(ledger.get("source"), dict)
        or ledger["source"].get("pdf_sha256") != PDF_SHA256
        or ledger.get("network_accessed") is not False
        or ledger.get("manual_inferences") != []
        or ledger.get("unresolved_ambiguities") != []
        or not isinstance(ledger.get("artifacts"), dict)
    ):
        raise ValueError("extraction ledger does not prove a clean mechanical split")
    artifacts = ledger["artifacts"]
    question = artifacts.get("question_pack")
    sealed = artifacts.get("sealed_reference")
    if (
        not isinstance(question, dict)
        or question.get("sha256") != _sha256(questions_path)
    ):
        raise ValueError("question extraction differs from its extraction ledger")
    if (
        not isinstance(sealed, dict)
        or sealed.get("sha256") != _sha256(sealed_reference_path)
    ):
        raise ValueError("sealed extraction differs from its extraction ledger")
    return ledger


def _question_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("extracted question ID is invalid")
    normalized = value.strip().lower()
    if not normalized.startswith("q") or not normalized[1:].isdigit():
        raise ValueError("extracted question ID is invalid")
    number = int(normalized[1:])
    if not 1 <= number <= 20:
        raise ValueError("extracted question ID is outside Q1..Q20")
    return f"Q{number}"


def _render_question_images(
    pdf_path: Path,
    question_rows: list[dict],
    image_root: Path,
) -> dict[str, list[str]]:
    declared_pages = {
        _question_id(row.get("question_id")): row.get("image_pages")
        for row in question_rows
    }
    if not any(declared_pages.values()):
        return {question_id: [] for question_id in declared_pages}
    try:
        import pdfplumber
    except ImportError as error:
        raise RuntimeError(
            "PDF image extraction requires the pinned document runtime"
        ) from error
    rendered = {question_id: [] for question_id in declared_pages}
    image_root.mkdir(parents=True, exist_ok=True)
    with pdfplumber.open(pdf_path) as pdf:
        for question_id, page_numbers in declared_pages.items():
            if not isinstance(page_numbers, list):
                raise ValueError("question image_pages must be a list")
            for page_number in page_numbers:
                if (
                    isinstance(page_number, bool)
                    or not isinstance(page_number, int)
                    or not 1 <= page_number <= len(pdf.pages)
                ):
                    raise ValueError("question image page is invalid")
                page = pdf.pages[page_number - 1]
                for index, image in enumerate(page.images, start=1):
                    bbox = (
                        float(image["x0"]),
                        float(image["top"]),
                        float(image["x1"]),
                        float(image["bottom"]),
                    )
                    target = (
                        image_root
                        / f"{question_id}-page-{page_number}-image-{index}.png"
                    )
                    page.crop(bbox).to_image(
                        resolution=144,
                        antialias=True,
                    ).save(target, format="PNG")
                    rendered[question_id].append(str(target))
            if page_numbers and not rendered[question_id]:
                raise ValueError(
                    f"declared question image page had no embedded image: {question_id}"
                )
    return rendered


def _merge_extraction(
    questions: list[dict],
    answers: list[dict],
    image_sources: dict[str, list[str]],
) -> dict:
    answer_by_id = {_question_id(row.get("question_id")): row for row in answers}
    if set(answer_by_id) != {f"Q{number}" for number in range(1, 21)}:
        raise ValueError("sealed answer population drifted")
    merged = []
    for question in questions:
        question_id = _question_id(question.get("question_id"))
        answer = answer_by_id[question_id]
        if set(question) != {
            "question_id",
            "question_body",
            "page_number",
            "image_pages",
        }:
            raise ValueError("question extraction fields drifted")
        if set(answer) != {
            "question_id",
            "answer_body",
            "answer_pages",
            "reference_claims",
        }:
            raise ValueError("sealed extraction fields drifted")
        merged.append(
            {
                "question_id": question_id,
                "question_body": question["question_body"],
                "page_number": question["page_number"],
                "image_sources": image_sources[question_id],
                "answer_body": answer["answer_body"],
                "answer_pages": answer["answer_pages"],
                "reference_claims": answer["reference_claims"],
            }
        )
    if [row["question_id"] for row in merged] != [
        f"Q{number}" for number in range(1, 21)
    ]:
        raise ValueError("question extraction population or order drifted")
    return {
        "contract": "extracted_real_question_corpus/v1",
        "pdf_sha256": PDF_SHA256,
        "questions": merged,
    }


def _write_json(path: Path, value: object, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(mode)


def _promote_output_tree(staging: Path, output_root: Path) -> None:
    backup = None
    try:
        if output_root.exists():
            if output_root.is_symlink():
                raise ValueError("real-question output root cannot be a symbolic link")
            backup = (
                output_root.parent
                / f".{output_root.name}.backup-{uuid.uuid4().hex}"
            )
            os.replace(output_root, backup)
        os.replace(staging, output_root)
    except Exception:
        if output_root.exists():
            shutil.rmtree(output_root, ignore_errors=True)
        if backup is not None and backup.exists():
            os.replace(backup, output_root)
        raise
    else:
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--sealed-reference", type=Path, required=True)
    parser.add_argument("--extraction-ledger", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    if _sha256(args.pdf) != PDF_SHA256:
        raise ValueError("real-question PDF bytes drifted")
    questions = _load_list(args.questions, "question extraction")
    answers = _load_list(args.sealed_reference, "sealed extraction")
    try:
        ledger = json.loads(args.extraction_ledger.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("extraction ledger is unreadable") from error
    validate_extraction_ledger_inputs(
        ledger,
        questions_path=args.questions,
        sealed_reference_path=args.sealed_reference,
    )

    output_root = args.output_root.resolve()
    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="real-question-pack-", dir=output_root.parent))
    try:
        image_sources = _render_question_images(
            args.pdf,
            questions,
            staging / "extracted-question-images",
        )
        extraction = _merge_extraction(questions, answers, image_sources)
        question_root = staging / "question-pack"
        sealed_root = staging / "sealed-reference"
        receipt = build_isolated_real_question_packages(
            extraction,
            question_root=question_root,
            sealed_root=sealed_root,
        )
        raw_root = staging / "raw-extraction"
        raw_root.mkdir(parents=True, exist_ok=True)
        raw_root.chmod(0o700)
        for source, name in (
            (args.questions, "question-pack.raw.json"),
            (args.sealed_reference, "sealed-reference.raw.json"),
            (args.extraction_ledger, "extraction-ledger.json"),
        ):
            shutil.copy2(source, raw_root / name)
            (raw_root / name).chmod(0o600)
        contamination = {
            "contract": "real_question_contamination_ledger/v1",
            "pdf_sha256": PDF_SHA256,
            "questions": {
                f"Q{number}": (
                    "contaminated_calibration_only"
                    if number == 2
                    else "scored_blind_baseline"
                )
                for number in range(1, 21)
            },
            "scored_question_count": 19,
            "blind_generalization_claim_permitted_after_reveal": False,
            "reason": (
                "Q2 participated in the prior real trial; after package reveal the "
                "entire PDF becomes development and regression evidence."
            ),
        }
        _write_json(staging / "contamination-ledger.json", contamination)
        packaged_ledger = build_packaged_extraction_ledger(
            ledger,
            source_ledger_path=args.extraction_ledger,
            source_ledger_sha256=_sha256(args.extraction_ledger),
            raw_question_path=args.questions,
            raw_sealed_path=args.sealed_reference,
            question_pack_path=(
                question_root / "real-question-pack.json"
            ),
            sealed_reference_path=(
                sealed_root / "sealed-reference-answer.json"
            ),
        )
        packaged_ledger_path = staging / "package-extraction-ledger.json"
        _write_json(packaged_ledger_path, packaged_ledger, mode=0o600)
        receipt["extraction_ledger_sha256"] = _sha256(
            packaged_ledger_path
        )
        receipt["source_extraction_ledger_sha256"] = _sha256(
            args.extraction_ledger
        )
        receipt["contamination_ledger_sha256"] = _sha256(
            staging / "contamination-ledger.json"
        )
        receipt.pop("receipt_sha256", None)
        receipt["receipt_sha256"] = _canonical_sha256(receipt)
        _write_json(
            staging / "package-build-receipt.json",
            receipt,
            mode=0o600,
        )
        _promote_output_tree(staging, output_root)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
