"""Deterministic question/reference separation for sealed real-question runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import tempfile
import urllib.parse
import uuid
import zlib
from copy import deepcopy
from pathlib import Path
from typing import Iterable, Mapping

from .real_question_contracts import (
    PDF_SHA256,
    QUESTION_DISPOSITIONS,
    QUESTION_IDS,
    SCORED_QUESTION_IDS,
)


EXTRACTION_CONTRACT = "extracted_real_question_corpus/v1"
MAX_PNG_DECODED_BYTES = 64 * 1024 * 1024
PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
PNG_BIT_DEPTHS = {
    0: {1, 2, 4, 8, 16},
    2: {8, 16},
    3: {1, 2, 4, 8},
    4: {8, 16},
    6: {8, 16},
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _atomic_write(path: Path, content: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def _validate_png_bytes(content: bytes) -> None:
    """Validate a bounded, non-interlaced PNG including every scanline."""

    if len(content) > 5 * 1024 * 1024 or not content.startswith(
        b"\x89PNG\r\n\x1a\n"
    ):
        raise ValueError("real-question image is not a bounded PNG")
    position = 8
    chunks: list[bytes] = []
    seen_ihdr = False
    seen_plte = False
    seen_idat = False
    idat_ended = False
    seen_iend = False
    width = height = bit_depth = color_type = None
    while position < len(content):
        if position + 12 > len(content):
            raise ValueError("real-question image PNG is truncated")
        length = struct.unpack(">I", content[position : position + 4])[0]
        chunk_type = content[position + 4 : position + 8]
        data_start = position + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(content):
            raise ValueError("real-question image PNG chunk is truncated")
        data = content[data_start:data_end]
        expected_crc = struct.unpack(">I", content[data_end:crc_end])[0]
        observed_crc = zlib.crc32(chunk_type)
        observed_crc = zlib.crc32(data, observed_crc) & 0xFFFFFFFF
        if observed_crc != expected_crc:
            raise ValueError("real-question image PNG checksum drifted")
        if chunk_type == b"IHDR":
            if seen_ihdr or position != 8 or length != 13:
                raise ValueError("real-question image PNG header is invalid")
            (
                width,
                height,
                bit_depth,
                color_type,
                compression,
                filter_method,
                interlace,
            ) = struct.unpack(">IIBBBBB", data)
            if width < 1 or height < 1 or width > 10000 or height > 10000:
                raise ValueError("real-question image dimensions are invalid")
            if (
                color_type not in PNG_CHANNELS
                or bit_depth not in PNG_BIT_DEPTHS[color_type]
                or compression != 0
                or filter_method != 0
                or interlace != 0
            ):
                raise ValueError("real-question image PNG header model is unsupported")
            seen_ihdr = True
        elif chunk_type == b"PLTE":
            if (
                not seen_ihdr
                or seen_plte
                or seen_idat
                or color_type in {0, 4}
                or length < 3
                or length > 768
                or length % 3
                or (
                    color_type == 3
                    and bit_depth is not None
                    and length // 3 > 2 ** int(bit_depth)
                )
            ):
                raise ValueError("real-question image PNG palette or order is invalid")
            seen_plte = True
        elif chunk_type == b"IDAT":
            if (
                not seen_ihdr
                or idat_ended
                or (color_type == 3 and not seen_plte)
            ):
                raise ValueError("real-question image PNG palette or order is invalid")
            seen_idat = True
            chunks.append(data)
        elif chunk_type == b"IEND":
            if length != 0 or not seen_idat:
                raise ValueError("real-question image PNG terminator is invalid")
            seen_iend = True
            position = crc_end
            break
        elif seen_idat:
            idat_ended = True
        position = crc_end
    if (
        not seen_ihdr
        or not seen_iend
        or not chunks
        or position != len(content)
    ):
        raise ValueError("real-question image PNG structure is incomplete")
    if None in (width, height, bit_depth, color_type):
        raise ValueError("real-question image PNG header is incomplete")
    row_bytes = (
        int(width) * PNG_CHANNELS[int(color_type)] * int(bit_depth) + 7
    ) // 8
    expected_decoded = int(height) * (row_bytes + 1)
    if expected_decoded > MAX_PNG_DECODED_BYTES:
        raise ValueError("real-question image decoded payload exceeds budget")
    decoder = zlib.decompressobj()
    try:
        decoded = decoder.decompress(
            b"".join(chunks), MAX_PNG_DECODED_BYTES + 1
        )
        if len(decoded) > MAX_PNG_DECODED_BYTES or decoder.unconsumed_tail:
            raise ValueError(
                "real-question image decoded payload exceeds budget"
            )
        decoded += decoder.flush(MAX_PNG_DECODED_BYTES + 1 - len(decoded))
    except zlib.error as error:
        raise ValueError("real-question image PNG payload cannot be decoded") from error
    if len(decoded) > MAX_PNG_DECODED_BYTES:
        raise ValueError("real-question image decoded payload exceeds budget")
    if (
        not decoder.eof
        or decoder.unused_data
        or len(decoded) != expected_decoded
    ):
        raise ValueError("real-question image PNG scanline length is invalid")
    stride = row_bytes + 1
    if any(decoded[offset] > 4 for offset in range(0, len(decoded), stride)):
        raise ValueError("real-question image PNG filter byte is invalid")


def _reject_root_symlink(path: Path, label: str) -> Path:
    supplied = Path(path)
    absolute = supplied if supplied.is_absolute() else Path.cwd() / supplied
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"{label} has a linked parent or root")
    return supplied


def _read_regular_no_follow(path: Path, label: str) -> bytes:
    supplied = Path(path)
    if supplied.is_symlink():
        raise ValueError(f"{label} is linked")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(supplied, flags)
    except OSError as error:
        raise ValueError(f"{label} is unreadable or linked") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{label} is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _tree_identity(root: Path) -> dict:
    root = _reject_root_symlink(Path(root), "real-question package tree").resolve()
    if not root.is_dir():
        raise ValueError("real-question package tree is absent or linked")
    files = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError("real-question package tree contains a symbolic link")
        if path.is_file():
            content = path.read_bytes()
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _sha256_bytes(content),
                    "bytes": len(content),
                }
            )
    body = {
        "contract": "real_question_package_tree_identity/v1",
        "file_count": len(files),
        "files": files,
    }
    body["tree_sha256"] = _canonical_sha256(files)
    return body


def validate_question_package_tree(
    question_root: Path,
    *,
    expected_tree_sha256: str | None = None,
) -> dict:
    """Verify exact registered files, image bytes and the complete executor tree."""

    question_root = _reject_root_symlink(
        Path(question_root), "real-question package root"
    ).resolve()
    manifest_path = question_root / "real-question-pack.json"
    manifest_bytes = _read_regular_no_follow(
        manifest_path, "real-question package manifest"
    )
    try:
        pack = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("real-question package manifest is unreadable") from error
    if (
        not isinstance(pack, Mapping)
        or pack.get("contract") != "real_question_pack/v1"
        or not isinstance(pack.get("questions"), list)
    ):
        raise ValueError("real-question package manifest is invalid")
    expected_files = {"real-question-pack.json"}
    file_snapshots = {
        "real-question-pack.json": manifest_bytes,
    }
    for question in pack["questions"]:
        if not isinstance(question, Mapping) or not isinstance(
            question.get("image_refs"), list
        ):
            raise ValueError("real-question image registry is invalid")
        for ref in question["image_refs"]:
            if (
                not isinstance(ref, Mapping)
                or set(ref) != {"path", "sha256"}
                or not isinstance(ref.get("path"), str)
                or not isinstance(ref.get("sha256"), str)
            ):
                raise ValueError("real-question image registry is invalid")
            relative = Path(ref["path"])
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or relative.suffix.lower() != ".png"
            ):
                raise ValueError("real-question image path is invalid")
            image_path = question_root / relative
            if not image_path.is_file() or image_path.is_symlink():
                raise ValueError("real-question image is absent or linked")
            content = _read_regular_no_follow(
                image_path, "real-question image"
            )
            _validate_png_bytes(content)
            if _sha256_bytes(content) != ref["sha256"]:
                raise ValueError("real-question image hash drifted")
            expected_files.add(relative.as_posix())
            file_snapshots[relative.as_posix()] = content
    observed_files = set()
    for path in question_root.rglob("*"):
        if path.is_symlink():
            raise ValueError("real-question package tree contains a symbolic link")
        if path.is_file():
            observed_files.add(path.relative_to(question_root).as_posix())
    if observed_files != expected_files:
        raise ValueError("real-question package contains unregistered files")
    files = [
        {
            "path": relative,
            "sha256": _sha256_bytes(file_snapshots[relative]),
            "bytes": len(file_snapshots[relative]),
        }
        for relative in sorted(file_snapshots)
    ]
    identity = {
        "contract": "real_question_package_tree_identity/v1",
        "file_count": len(files),
        "files": files,
        "tree_sha256": _canonical_sha256(files),
    }
    if (
        expected_tree_sha256 is not None
        and identity["tree_sha256"] != expected_tree_sha256
    ):
        raise ValueError("real-question package tree identity drifted")
    return {
        "contract": "real_question_package_tree_verification/v1",
        "status": "valid",
        "question_pack_sha256": _sha256_bytes(manifest_bytes),
        "tree_sha256": identity["tree_sha256"],
        "file_count": identity["file_count"],
        "question_pack": deepcopy(dict(pack)),
    }


def _assert_disjoint(question_root: Path, sealed_root: Path) -> tuple[Path, Path]:
    question_root = _reject_root_symlink(
        Path(question_root), "question package root"
    ).resolve()
    sealed_root = _reject_root_symlink(
        Path(sealed_root), "reference package root"
    ).resolve()
    if (
        question_root == sealed_root
        or question_root in sealed_root.parents
        or sealed_root in question_root.parents
    ):
        raise ValueError("question and sealed reference roots must be disjoint")
    return question_root, sealed_root


def _validate_extraction(extraction: Mapping[str, object]) -> list[dict]:
    if (
        not isinstance(extraction, Mapping)
        or set(extraction) != {"contract", "pdf_sha256", "questions"}
        or extraction.get("contract") != EXTRACTION_CONTRACT
        or extraction.get("pdf_sha256") != PDF_SHA256
    ):
        raise ValueError("real-question extraction contract or PDF binding is invalid")
    rows = extraction.get("questions")
    if not isinstance(rows, list) or len(rows) != len(QUESTION_IDS):
        raise ValueError("real-question extraction must contain exactly 20 rows")
    normalized: list[dict] = []
    expected_fields = {
        "question_id",
        "question_body",
        "page_number",
        "image_sources",
        "answer_body",
        "answer_pages",
        "reference_claims",
    }
    observed: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != expected_fields:
            raise ValueError("real-question extraction row is incomplete")
        value = deepcopy(dict(row))
        question_id = value.get("question_id")
        observed.append(str(question_id))
        if (
            question_id not in QUESTION_DISPOSITIONS
            or not isinstance(value.get("question_body"), str)
            or not value["question_body"].strip()
            or isinstance(value.get("page_number"), bool)
            or not isinstance(value.get("page_number"), int)
            or value["page_number"] < 1
            or not isinstance(value.get("answer_body"), str)
            or not value["answer_body"].strip()
        ):
            raise ValueError("real-question extraction row identity or text is invalid")
        image_sources = value.get("image_sources")
        if not isinstance(image_sources, list):
            raise ValueError("real-question extraction image sources are invalid")
        for source in image_sources:
            if (
                not isinstance(source, str)
                or not Path(source).is_file()
                or Path(source).is_symlink()
                or Path(source).suffix.lower() != ".png"
            ):
                raise ValueError("real-question extraction image source is absent")
            _validate_png_bytes(Path(source).read_bytes())
        answer_pages = value.get("answer_pages")
        if (
            not isinstance(answer_pages, list)
            or not answer_pages
            or any(
                isinstance(page, bool) or not isinstance(page, int) or page < 1
                for page in answer_pages
            )
        ):
            raise ValueError("real-question answer page binding is invalid")
        claims = value.get("reference_claims")
        if not isinstance(claims, list) or any(
            not isinstance(claim, str) or not claim.strip() for claim in claims
        ):
            raise ValueError("real-question reference claims are invalid")
        normalized.append(value)
    if observed != QUESTION_IDS:
        raise ValueError("real-question extraction population or order drifted")
    normalized_answers = [
        " ".join(str(row["answer_body"]).split()).casefold() for row in normalized
    ]
    for row in normalized:
        question_text = " ".join(str(row["question_body"]).split()).casefold()
        for answer_text in normalized_answers:
            if question_text == answer_text or (
                len(answer_text) >= 64 and answer_text in question_text
            ):
                raise ValueError("real-question extraction contains answer leakage")
    return normalized


def build_isolated_real_question_packages(
    extraction: Mapping[str, object],
    *,
    question_root: Path,
    sealed_root: Path,
) -> dict:
    """Split one extraction into executor-visible and grader-only packages."""

    question_root, sealed_root = _assert_disjoint(question_root, sealed_root)
    rows = _validate_extraction(extraction)
    question_root.parent.mkdir(parents=True, exist_ok=True)
    sealed_root.parent.mkdir(parents=True, exist_ok=True)
    if question_root.is_symlink() or sealed_root.is_symlink():
        raise ValueError("real-question package root cannot be a symbolic link")
    staged_question = Path(
        tempfile.mkdtemp(prefix=".real-question-question-", dir=question_root.parent)
    )
    staged_sealed = Path(
        tempfile.mkdtemp(prefix=".real-question-sealed-", dir=sealed_root.parent)
    )
    os.chmod(staged_question, 0o755)
    os.chmod(staged_sealed, 0o700)
    questions = []
    answers = []
    try:
        for row in rows:
            question_id = row["question_id"]
            image_refs = []
            for index, source_text in enumerate(row["image_sources"], start=1):
                source = Path(source_text)
                relative = Path("images") / f"{question_id}-{index}.png"
                content = source.read_bytes()
                _validate_png_bytes(content)
                _atomic_write(staged_question / relative, content, mode=0o644)
                image_refs.append(
                    {"path": relative.as_posix(), "sha256": _sha256_bytes(content)}
                )
            questions.append(
                {
                    "question_id": question_id,
                    "disposition": QUESTION_DISPOSITIONS[question_id],
                    "question_body": row["question_body"].strip(),
                    "page_number": row["page_number"],
                    "image_refs": image_refs,
                }
            )
            answer_body = row["answer_body"].strip()
            answer_relative = Path("answers") / f"{question_id}.txt"
            _atomic_write(
                staged_sealed / answer_relative,
                answer_body.encode("utf-8"),
                mode=0o600,
            )
            answers.append(
                {
                    "question_id": question_id,
                    "answer_body": answer_body,
                    "answer_path": answer_relative.as_posix(),
                    "answer_sha256": _sha256_bytes(answer_body.encode("utf-8")),
                    "page_numbers": deepcopy(row["answer_pages"]),
                    "reference_claims": [
                        claim.strip() for claim in row["reference_claims"]
                    ],
                }
            )
        question_pack = {
            "contract": "real_question_pack/v1",
            "pdf_sha256": PDF_SHA256,
            "questions": questions,
            "scored_question_ids": SCORED_QUESTION_IDS,
        }
        sealed_reference = {
            "contract": "sealed_reference_answer/v1",
            "pdf_sha256": PDF_SHA256,
            "visibility": "grader_only",
            "answers": answers,
        }
        staged_question_path = staged_question / "real-question-pack.json"
        staged_sealed_path = staged_sealed / "sealed-reference-answer.json"
        _atomic_write(
            staged_question_path,
            _canonical_bytes(question_pack) + b"\n",
            mode=0o644,
        )
        _atomic_write(
            staged_sealed_path,
            _canonical_bytes(sealed_reference) + b"\n",
            mode=0o600,
        )
        question_verification = validate_question_package_tree(staged_question)
        sealed_identity = _tree_identity(staged_sealed)
        _atomic_replace_package_pair(
            (
                (staged_question, question_root),
                (staged_sealed, sealed_root),
            )
        )
        promoted = validate_question_package_tree(
            question_root,
            expected_tree_sha256=question_verification["tree_sha256"],
        )
        sealed_path = sealed_root / "sealed-reference-answer.json"
        receipt = {
            "contract": "real_question_package_split_receipt/v1",
            "pdf_sha256": PDF_SHA256,
            "question_count": len(questions),
            "scored_question_count": len(SCORED_QUESTION_IDS),
            "question_pack_sha256": promoted["question_pack_sha256"],
            "question_tree_sha256": promoted["tree_sha256"],
            "sealed_reference_sha256": _sha256_bytes(sealed_path.read_bytes()),
            "sealed_tree_sha256": sealed_identity["tree_sha256"],
            "roots_are_disjoint": True,
        }
        receipt["receipt_sha256"] = _canonical_sha256(receipt)
        return receipt
    finally:
        shutil.rmtree(staged_question, ignore_errors=True)
        shutil.rmtree(staged_sealed, ignore_errors=True)


def build_packaged_extraction_ledger(
    source_ledger: Mapping[str, object],
    *,
    source_ledger_path: Path,
    source_ledger_sha256: str,
    raw_question_path: Path,
    raw_sealed_path: Path,
    question_pack_path: Path,
    sealed_reference_path: Path,
) -> dict:
    """Bind final split bytes without rewriting the extractor's raw ledger."""

    if (
        not isinstance(source_ledger, Mapping)
        or source_ledger.get("schema")
        != "pdf_question_answer_extraction_ledger/v1"
        or not isinstance(source_ledger.get("source"), Mapping)
        or source_ledger["source"].get("pdf_sha256") != PDF_SHA256
        or source_ledger.get("network_accessed") is not False
        or source_ledger.get("manual_inferences") != []
        or source_ledger.get("unresolved_ambiguities") != []
        or not isinstance(source_ledger.get("artifacts"), Mapping)
    ):
        raise ValueError("source extraction ledger is not an accepted clean split")
    source_ledger_path = Path(source_ledger_path)
    if (
        not source_ledger_path.is_file()
        or source_ledger_path.is_symlink()
        or not isinstance(source_ledger_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", source_ledger_sha256) is None
        or _sha256_bytes(source_ledger_path.read_bytes())
        != source_ledger_sha256
    ):
        raise ValueError("source extraction ledger identity is invalid")
    try:
        recorded_source_ledger = json.loads(
            source_ledger_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("source extraction ledger is unreadable") from error
    if recorded_source_ledger != source_ledger:
        raise ValueError("source extraction ledger object drifted")
    artifacts = source_ledger["artifacts"]
    raw_question = artifacts.get("question_pack")
    raw_sealed = artifacts.get("sealed_reference")
    if (
        not isinstance(raw_question, Mapping)
        or raw_question.get("sha256")
        != _sha256_bytes(Path(raw_question_path).read_bytes())
        or not isinstance(raw_sealed, Mapping)
        or raw_sealed.get("sha256")
        != _sha256_bytes(Path(raw_sealed_path).read_bytes())
    ):
        raise ValueError("raw extraction bytes drifted from their source ledger")
    try:
        raw_answers = json.loads(
            Path(raw_sealed_path).read_text(encoding="utf-8")
        )
        final_sealed = json.loads(
            Path(sealed_reference_path).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("sealed answer content is unreadable") from error
    final_answers = (
        final_sealed.get("answers")
        if isinstance(final_sealed, Mapping)
        else None
    )
    if not isinstance(raw_answers, list) or not isinstance(final_answers, list):
        raise ValueError("sealed answer content contract is invalid")

    def answer_rows(rows: list[object], *, raw: bool) -> list[tuple[str, str]]:
        normalized = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, Mapping):
                raise ValueError("sealed answer content row is invalid")
            question_id = row.get("question_id")
            answer_body = row.get("answer_body")
            if not isinstance(question_id, str) or not isinstance(
                answer_body, str
            ):
                raise ValueError("sealed answer content row is incomplete")
            expected_id = f"Q{index}"
            normalized_id = (
                f"Q{int(question_id[1:])}"
                if raw
                and re.fullmatch(r"q[0-9]{2}", question_id) is not None
                else question_id
            )
            if normalized_id != expected_id:
                raise ValueError("sealed answer content order drifted")
            normalized.append((normalized_id, answer_body.strip()))
        return normalized

    raw_answer_rows = answer_rows(raw_answers, raw=True)
    final_answer_rows = answer_rows(final_answers, raw=False)
    if raw_answer_rows != final_answer_rows:
        raise ValueError("sealed answer content changed during package split")
    answer_equivalence_sha256 = _canonical_sha256(raw_answer_rows)
    return {
        "schema": "pdf_question_answer_extraction_ledger/v1",
        "source": deepcopy(dict(source_ledger["source"])),
        "network_accessed": False,
        "manual_inferences": [],
        "unresolved_ambiguities": [],
        "artifacts": {
            "question_pack": {
                "path": "question-pack/real-question-pack.json",
                "sha256": _sha256_bytes(Path(question_pack_path).read_bytes()),
            },
            "sealed_reference": {
                "path": "sealed-reference/sealed-reference-answer.json",
                "sha256": _sha256_bytes(
                    Path(sealed_reference_path).read_bytes()
                ),
            },
            "raw_question_pack": deepcopy(dict(raw_question)),
            "raw_sealed_reference": deepcopy(dict(raw_sealed)),
            "source_extraction_ledger": {
                "sha256": source_ledger_sha256,
            },
        },
        "derivation": {
            "kind": "mechanical_isolated_package_split",
            "source_ledger_sha256": source_ledger_sha256,
            "answer_content_changed": False,
            "answer_content_equivalence_sha256": (
                answer_equivalence_sha256
            ),
        },
    }


def _atomic_replace_package_pair(
    pairs: tuple[tuple[Path, Path], tuple[Path, Path]],
) -> None:
    backups: list[tuple[Path, Path]] = []
    promoted: list[Path] = []
    try:
        for _, target in pairs:
            if target.exists():
                backup = target.parent / f".{target.name}.backup-{uuid.uuid4().hex}"
                os.replace(target, backup)
                backups.append((backup, target))
        for staged, target in pairs:
            os.replace(staged, target)
            promoted.append(target)
    except Exception:
        for target in reversed(promoted):
            shutil.rmtree(target, ignore_errors=True)
        for backup, target in reversed(backups):
            if backup.exists():
                os.replace(backup, target)
        raise
    else:
        for backup, _ in backups:
            shutil.rmtree(backup, ignore_errors=True)


def build_blind_run_manifest(
    *,
    question_pack_sha256: str,
    question_tree_sha256: str,
    code_sha256: str,
    model_id: str,
    model_settings_sha256: str,
    budget_sha256: str,
    official_source_snapshot_sha256: str,
    execution_identity: Mapping[str, object],
    question_read_authorization_receipt: Mapping[str, object],
) -> dict:
    policy_sha256 = execution_identity.get("policy_sha256")
    for value, label in (
        (question_pack_sha256, "question pack"),
        (question_tree_sha256, "question tree"),
        (code_sha256, "code"),
        (model_settings_sha256, "model settings"),
        (budget_sha256, "budget"),
        (official_source_snapshot_sha256, "official source snapshot"),
        (policy_sha256, "execution policy"),
    ):
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"blind run {label} SHA-256 is invalid")
    if (
        execution_identity.get("contract")
        != "lca_research_kernel.project_execution_identity/v1"
        or not isinstance(execution_identity.get("execution_id"), str)
    ):
        raise ValueError("blind run execution identity is invalid")
    authorization = deepcopy(dict(question_read_authorization_receipt))
    authorization_sha256 = authorization.get("receipt_sha256")
    if (
        authorization.get("contract")
        != "lca_research_kernel.execution_authorization/v1"
        or not isinstance(authorization_sha256, str)
    ):
        raise ValueError("blind run question read authorization is invalid")
    return {
        "contract": "blind_run_manifest/v1",
        "pdf_sha256": PDF_SHA256,
        "question_ids": SCORED_QUESTION_IDS,
        "question_dispositions": QUESTION_DISPOSITIONS,
        "question_pack_sha256": question_pack_sha256,
        "question_tree_sha256": question_tree_sha256,
        "code_sha256": code_sha256,
        "model_id": model_id,
        "model_settings_sha256": model_settings_sha256,
        "permission_policy_sha256": policy_sha256,
        "budget_sha256": budget_sha256,
        "official_source_snapshot_sha256": official_source_snapshot_sha256,
        "execution_identity_sha256": _canonical_sha256(execution_identity),
        "question_read_authorization_sha256": authorization_sha256,
        "sealed_reference_access": "forbidden",
    }


def _walk(value: object) -> Iterable[tuple[str | None, object]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield None, item
            yield from _walk(item)


def _validate_blind_package_identity(
    *,
    question_root: Path,
    sealed_root: Path,
    sealed_package_receipt_path: Path,
    accepted_package_receipt_sha256: str,
    extraction_ledger_path: Path,
    question_tree_verification: Mapping[str, object],
) -> dict:
    receipt_bytes = _read_regular_no_follow(
        _reject_root_symlink(
            Path(sealed_package_receipt_path), "sealed package receipt"
        ),
        "sealed package receipt",
    )
    if _sha256_bytes(receipt_bytes) != accepted_package_receipt_sha256:
        raise ValueError("sealed package receipt differs from accepted identity")
    try:
        receipt = json.loads(receipt_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("sealed package receipt is unreadable") from error
    unsigned = deepcopy(dict(receipt)) if isinstance(receipt, Mapping) else {}
    stored = unsigned.pop("receipt_sha256", None)
    if (
        receipt.get("contract") != "real_question_package_split_receipt/v1"
        or receipt.get("pdf_sha256") != PDF_SHA256
        or receipt.get("roots_are_disjoint") is not True
        or stored != _canonical_sha256(unsigned)
        or receipt.get("question_pack_sha256")
        != question_tree_verification.get("question_pack_sha256")
        or receipt.get("question_tree_sha256")
        != question_tree_verification.get("tree_sha256")
    ):
        raise ValueError("sealed package receipt does not bind the question package")
    ledger_bytes = _read_regular_no_follow(
        _reject_root_symlink(Path(extraction_ledger_path), "extraction ledger"),
        "extraction ledger",
    )
    try:
        ledger = json.loads(ledger_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("extraction ledger is unreadable") from error
    if (
        _sha256_bytes(ledger_bytes) != receipt.get("extraction_ledger_sha256")
        or not isinstance(ledger, Mapping)
        or ledger.get("schema") != "pdf_question_answer_extraction_ledger/v1"
        or not isinstance(ledger.get("source"), Mapping)
        or ledger["source"].get("pdf_sha256") != PDF_SHA256
    ):
        raise ValueError("extraction ledger differs from accepted package identity")
    sealed_root = _reject_root_symlink(
        Path(sealed_root), "sealed package root"
    ).resolve()
    sealed_path = sealed_root / "sealed-reference-answer.json"
    sealed_bytes = _read_regular_no_follow(sealed_path, "sealed reference")
    sealed_tree = _tree_identity(sealed_root)
    if (
        _sha256_bytes(sealed_bytes) != receipt.get("sealed_reference_sha256")
        or sealed_tree.get("tree_sha256") != receipt.get("sealed_tree_sha256")
    ):
        raise ValueError("sealed package identity differs from accepted receipt")
    return {
        "accepted_package_receipt_sha256": accepted_package_receipt_sha256,
        "extraction_ledger_sha256": _sha256_bytes(ledger_bytes),
        "sealed_reference_sha256": receipt["sealed_reference_sha256"],
        "sealed_tree_sha256": receipt["sealed_tree_sha256"],
        "sealed_member_sha256s": [
            row["sha256"] for row in sealed_tree["files"]
        ],
    }
def validate_blind_execution_isolation(
    manifest: Mapping[str, object],
    *,
    execution_environment: object,
    question_root: Path,
    sealed_root: Path,
    question_read_authorization_receipt: Mapping[str, object],
    sealed_identifiers: set[str],
    sealed_package_receipt_path: Path,
    accepted_package_receipt_sha256: str,
    extraction_ledger_path: Path,
    question_tree_verification: Mapping[str, object] | None = None,
) -> dict:
    """Fail closed if any blind input can identify or reach the answer package."""

    forbidden_keys = {
        "answer_body",
        "answer_path",
        "answer_sha256",
        "answers",
        "reference_answer",
        "sealed_reference",
    }
    tree = (
        validate_question_package_tree(
            question_root,
            expected_tree_sha256=manifest.get("question_tree_sha256"),
        )
        if question_tree_verification is None
        else deepcopy(dict(question_tree_verification))
    )
    package_identity = _validate_blind_package_identity(
        question_root=question_root,
        sealed_root=sealed_root,
        sealed_package_receipt_path=sealed_package_receipt_path,
        accepted_package_receipt_sha256=accepted_package_receipt_sha256,
        extraction_ledger_path=extraction_ledger_path,
        question_tree_verification=tree,
    )
    normalized_identifiers = set()
    for identifier in {
        *sealed_identifiers,
        str(Path(sealed_root).resolve()),
        Path(sealed_root).name,
        "sealed-reference-answer.json",
        package_identity["sealed_reference_sha256"],
        package_identity["sealed_tree_sha256"],
    }:
        if isinstance(identifier, str) and identifier:
            normalized_identifiers.add(_normalize_identifier(identifier))
    for key, value in _walk(manifest):
        normalized_value = (
            _normalize_identifier(value) if isinstance(value, str) else None
        )
        if key in forbidden_keys or (
            normalized_value
            and any(
                identifier and identifier in normalized_value
                for identifier in normalized_identifiers
            )
        ):
            raise ValueError("blind manifest contains a sealed reference identifier")
    sealed_root = Path(sealed_root).resolve()
    question_root = Path(question_root).resolve()
    identity = getattr(execution_environment, "identity", None)
    policy = getattr(execution_environment, "policy", None)
    project_root = getattr(execution_environment, "project_root", None)
    if (
        not isinstance(identity, Mapping)
        or not isinstance(policy, Mapping)
        or not isinstance(project_root, Path)
    ):
        raise ValueError("blind execution environment identity is absent")
    roots = [
        (project_root / relative).resolve()
        for relative in policy["file_rules"]["read_roots"]
    ]
    for root in roots:
        if root == sealed_root or root in sealed_root.parents or sealed_root in root.parents:
            raise ValueError("executor read root reaches the sealed reference root")
        if root.is_dir():
            for member in root.rglob("*"):
                if member.is_symlink():
                    raise ValueError("executor read root contains a linked member")
                if member.is_file() and (
                    member.name.casefold() == "sealed-reference-answer.json"
                    or _sha256_bytes(member.read_bytes())
                    in package_identity["sealed_member_sha256s"]
                ):
                    raise ValueError(
                        "sealed package is reachable from executor read root"
                    )
    if not any(question_root == root or root in question_root.parents for root in roots):
        raise ValueError("question package is outside the actual executor read roots")
    if manifest.get("execution_identity_sha256") != _canonical_sha256(identity):
        raise ValueError("blind execution identity drifted")
    if manifest.get("permission_policy_sha256") != getattr(
        execution_environment, "policy_sha256", None
    ):
        raise ValueError("blind execution policy drifted")
    receipt = deepcopy(dict(question_read_authorization_receipt))
    authoritative_receipt = execution_environment.authorize_file(
        question_root / "real-question-pack.json",
        "read",
    )
    if receipt != authoritative_receipt:
        raise ValueError("blind question read authorization was not issued by runtime")
    receipt_sha256 = receipt.pop("receipt_sha256", None)
    if (
        receipt_sha256 != _canonical_sha256(receipt)
        or manifest.get("question_read_authorization_sha256") != receipt_sha256
        or receipt.get("contract")
        != "lca_research_kernel.execution_authorization/v1"
        or receipt.get("status") != "authorized"
        or receipt.get("kind") != "file"
        or receipt.get("execution_id") != identity.get("execution_id")
        or receipt.get("policy_sha256")
        != getattr(execution_environment, "policy_sha256", None)
        or not isinstance(receipt.get("payload"), Mapping)
        or receipt["payload"].get("operation") != "read"
        or Path(str(receipt["payload"].get("path"))).resolve()
        != question_root / "real-question-pack.json"
    ):
        raise ValueError("blind question read authorization receipt drifted")
    if tree["question_pack_sha256"] != manifest.get("question_pack_sha256"):
        raise ValueError("blind question pack identity drifted")
    if manifest.get("sealed_reference_access") != "forbidden":
        raise ValueError("blind manifest sealed reference access is not forbidden")
    return {
        "contract": "blind_execution_isolation_check/v1",
        "status": "passed",
        "executor_read_roots": [root.as_posix() for root in roots],
        "execution_identity_sha256": manifest["execution_identity_sha256"],
        "question_tree_sha256": tree["tree_sha256"],
        "question_read_authorization_sha256": receipt_sha256,
        "sealed_reference_access": "forbidden",
        "accepted_package_receipt_sha256": package_identity[
            "accepted_package_receipt_sha256"
        ],
        "extraction_ledger_sha256": package_identity[
            "extraction_ledger_sha256"
        ],
        "sealed_tree_sha256": package_identity["sealed_tree_sha256"],
        # This is the exact no-follow snapshot validated above.  Callers must
        # use it rather than reopening the mutable manifest path.
        "question_pack": tree["question_pack"],
        "question_pack_sha256": tree["question_pack_sha256"],
    }


def _normalize_identifier(value: str) -> str:
    normalized = value
    for _ in range(3):
        decoded = urllib.parse.unquote(normalized)
        if decoded == normalized:
            break
        normalized = decoded
    return normalized.replace("\\", "/").casefold()


__all__ = [
    "build_blind_run_manifest",
    "build_isolated_real_question_packages",
    "build_packaged_extraction_ledger",
    "validate_question_package_tree",
    "validate_blind_execution_isolation",
]
