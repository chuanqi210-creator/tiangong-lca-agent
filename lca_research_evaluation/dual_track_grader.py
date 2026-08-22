"""Grade only live-verified raw members against official and reference evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import urllib.parse
from copy import deepcopy
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Mapping

from lca_research_kernel.official_public_source import (
    ALLOWED_HOSTS,
    COMPOSITE_KEY_FIELDS,
    OfficialPublicSourceCapability,
)
from lca_research_kernel.claim_semantics import (
    CLAIM_MODALITIES,
    resolve_claim_modality,
    unbound_user_visible_legal_clauses,
)

from .real_question_contracts import (
    EXACT_DIMENSIONS,
    HARD_VETOES,
    PDF_SHA256,
    SCORED_QUESTION_IDS,
    load_real_question_optimization_contract,
    validate_real_question_artifact,
)
from .real_question_evidence_invalidation import (
    verify_real_question_raw_evidence_lock,
)
from .evidence_dag import canonical_tree_identity


# The grader must accept exactly the hosts whose receipts the execution
# capability is authorized to produce. A second allowlist drifted when the
# official Publications Office became the fail-closed EUR-Lex fallback.
OFFICIAL_HOSTS = ALLOWED_HOSTS
QUALITY_COMPONENT_MAXIMA = {
    "direct_conclusion": 15,
    "official_source_binding": 20,
    "exact_numeric_unit_time": 20,
    "exceptions_and_missing_data": 15,
    "legal_modality": 10,
    "commercial_actionability": 10,
    "clarity_and_concision": 10,
}
REFERENCE_RISKS = {"outdated", "overly_absolute", "official_conflict"}
TASK_ADAPTIVE_DOCUMENT_DIMENSION = "document"
TASK_ADAPTIVE_OFFICIAL_DIMENSIONS = frozenset(
    [*EXACT_DIMENSIONS, TASK_ADAPTIVE_DOCUMENT_DIMENSION]
)
SOURCE_REF_FIELDS = {
    "raw_path",
    "raw_sha256",
    "receipt_path",
    "receipt_sha256",
    "excerpt_path",
    "excerpt_sha256",
}
RAW_ROW_FIELDS = {
    "contract",
    "question_id",
    "disposition",
    "research_id",
    "input_basis_sha256",
    "request_sha256",
    "blind_manifest_sha256",
    "question_tree_sha256",
    "execution_identity_sha256",
    "status",
    "start_status",
    "status_view",
    "answer_view",
    "answer_sha256",
    "gradeable_answer_snapshot",
    "timing_receipts",
    "trace_refs",
    "project_record_refs",
    "official_source_refs",
    "product_interface_calls",
    "failure",
    "recorded_at",
    "raw_row_sha256",
}
TRACE_SNAPSHOT_FIELDS = {
    "contract",
    "question_id",
    "research_id",
    "request_sha256",
    "status",
    "start_status",
    "status_view",
    "timing_receipts",
    "trace_refs",
    "project_record_refs",
    "effect_counts",
    "effect_evidence",
    "events",
    "product_interface_calls",
    "failure",
    "recorded_at",
    "trace_snapshot_sha256",
}


def derive_task_adaptive_official_dimensions(
    result: Mapping[str, object],
) -> tuple[str, ...]:
    """Derive exactness gates from result semantics without case-specific routing."""

    values = result.get("values")
    claims = result.get("claim_statements")
    source_refs = result.get("official_source_refs")
    if not isinstance(values, list):
        raise ValueError("accepted result values are invalid")
    if values:
        required_value_fields = {
            "identity",
            "value",
            "unit",
            "source_sha256",
            "time_or_version",
        }
        if any(
            not isinstance(value, Mapping)
            or not required_value_fields.issubset(value)
            for value in values
        ):
            legacy_shape = result.get("status") == "candidate_only" and all(
                isinstance(value, Mapping)
                and set(value) == {"dimension", "value"}
                for value in values
            )
            if not legacy_shape:
                raise ValueError("quantitative result lacks a complete value binding")
        return tuple(EXACT_DIMENSIONS)
    if (
        isinstance(claims, list)
        and claims
        and isinstance(source_refs, list)
        and source_refs
    ):
        return (TASK_ADAPTIVE_DOCUMENT_DIMENSION,)
    raise ValueError("accepted result has no gradeable official evidence shape")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} is not a lowercase SHA-256")
    return value


def _score(value: object, maximum: float, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} score is invalid")
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or number > Decimal(str(maximum)):
        raise ValueError(f"{label} score is outside its declared range")
    return number


def _read_regular_no_follow(
    path: Path,
    *,
    label: str,
    private: bool = False,
) -> bytes:
    supplied = Path(path)
    if supplied.is_symlink() or supplied.parent.is_symlink():
        raise ValueError(f"{label} parent or file is linked")
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
        if private and metadata.st_mode & 0o077:
            raise ValueError(f"{label} permissions are not grader-only")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _parse_json_bytes(content: bytes, label: str) -> dict:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid JSON") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return deepcopy(dict(value))


def _raw_member_path(root: Path, relative_text: object, label: str) -> Path:
    if not isinstance(relative_text, str) or not relative_text or "\\" in relative_text:
        raise ValueError(f"{label} path is invalid")
    relative = PurePosixPath(relative_text)
    if (
        relative.is_absolute()
        or relative_text != relative.as_posix()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{label} path is invalid")
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"{label} is absent or linked")
    if root not in candidate.resolve().parents:
        raise ValueError(f"{label} escaped the raw tree")
    return candidate


def _read_raw_member(
    raw_root: Path,
    relative_path: object,
    expected_sha256: object,
    label: str,
) -> bytes:
    expected = _sha(expected_sha256, f"{label} member")
    content = _read_regular_no_follow(
        _raw_member_path(raw_root, relative_path, label),
        label=label,
    )
    if _sha256_bytes(content) != expected:
        raise ValueError(f"{label} member hash drifted")
    return content


def _validate_source_receipt(value: object) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError("official source receipt is absent")
    receipt = deepcopy(dict(value))
    stored = receipt.pop("receipt_sha256", None)
    if (
        value.get("contract") != "official_public_source_receipt/v1"
        or not isinstance(stored, str)
        or stored != _canonical_sha256(receipt)
    ):
        raise ValueError("official source receipt identity drifted")
    parsed = urllib.parse.urlsplit(str(value.get("final_url", "")))
    if (
        parsed.scheme != "https"
        or parsed.hostname not in OFFICIAL_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.fragment
        or value.get("status") != 200
    ):
        raise ValueError("official source receipt is not an exact official read")
    if not isinstance(value.get("accessed_at"), str) or not value[
        "accessed_at"
    ].strip():
        raise ValueError("official source receipt access time is absent")
    if not isinstance(value.get("parse_location"), str) or not value[
        "parse_location"
    ].strip():
        raise ValueError("official source receipt parse location is absent")
    _sha(value.get("body_sha256"), "official source body")
    if (
        isinstance(value.get("body_bytes"), bool)
        or not isinstance(value.get("body_bytes"), int)
        or value["body_bytes"] < 0
    ):
        raise ValueError("official source receipt body size is invalid")
    return deepcopy(dict(value))


def _sealed_identity(path: Path) -> tuple[str, dict[str, dict]]:
    content = _read_regular_no_follow(
        path,
        label="sealed reference",
        private=True,
    )
    sealed = _parse_json_bytes(content, "sealed reference")
    contract = load_real_question_optimization_contract(
        Path(__file__).resolve().parents[1]
    )
    validate_real_question_artifact(sealed, contract)
    identities = {}
    for row in sealed["answers"]:
        identities[row["question_id"]] = {
            "reference_answer_sha256": row["answer_sha256"],
            "reference_claims_sha256": _canonical_sha256(
                row["reference_claims"]
            ),
            "reference_answer_body": row["answer_body"],
        }
    return _sha256_bytes(content), identities


def _verified_evidence_ref(
    raw_root: Path,
    run: Mapping[str, object],
    value: object,
    *,
    label: str,
) -> dict:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise ValueError(f"{label} evidence reference is invalid")
    allowed = {
        (run["answer_path"], run["answer_sha256"]),
        (run["trace_path"], run["trace_sha256"]),
    }
    for source in run["official_source_refs"]:
        allowed.update(
            {
                (source["raw_path"], source["raw_sha256"]),
                (source["receipt_path"], source["receipt_sha256"]),
                (source["excerpt_path"], source["excerpt_sha256"]),
            }
        )
    identity = (value.get("path"), value.get("sha256"))
    if identity not in allowed:
        raise ValueError(f"{label} evidence is not a verified question member")
    _read_raw_member(raw_root, identity[0], identity[1], label)
    return deepcopy(dict(value))


def _validate_bound_check(
    value: object,
    *,
    raw_root: Path,
    run: Mapping[str, object],
    label: str,
) -> tuple[bool, dict]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"status", "evidence_ref"}
        or value.get("status") not in {"passed", "failed"}
    ):
        raise ValueError(f"{label} evidence check is invalid")
    evidence_ref = _verified_evidence_ref(
        raw_root,
        run,
        value["evidence_ref"],
        label=label,
    )
    return value["status"] == "passed", evidence_ref


def _validate_veto_checks(
    value: object,
    *,
    raw_root: Path,
    run: Mapping[str, object],
) -> tuple[dict[str, bool], list[dict]]:
    if not isinstance(value, list) or len(value) != len(HARD_VETOES):
        raise ValueError("hard-veto evidence checks are incomplete")
    observed = []
    normalized = {}
    refs = []
    for row in value:
        if (
            not isinstance(row, Mapping)
            or set(row) != {"veto_id", "status", "evidence_ref"}
            or row.get("veto_id") not in HARD_VETOES
        ):
            raise ValueError("hard-veto evidence check is invalid")
        veto = str(row["veto_id"])
        observed.append(veto)
        passed, evidence_ref = _validate_bound_check(
            {"status": row["status"], "evidence_ref": row["evidence_ref"]},
            raw_root=raw_root,
            run=run,
            label=f"hard veto {veto}",
        )
        normalized[veto] = passed
        refs.append({"veto_id": veto, "evidence_ref": evidence_ref})
    if observed != HARD_VETOES:
        raise ValueError("hard-veto evidence check order drifted")
    return normalized, refs


def _official_source_evidence(
    *,
    raw_root: Path,
    question_id: str,
    run: Mapping[str, object],
    source_ref: object,
    dimension: str,
    run_trace_refs: object,
) -> tuple[dict, dict, bytes]:
    if (
        not isinstance(source_ref, Mapping)
        or set(source_ref) != SOURCE_REF_FIELDS
        or dict(source_ref) not in run["official_source_refs"]
    ):
        raise ValueError("official source reference is not a registered member")
    raw_body = _read_raw_member(
        raw_root,
        source_ref["raw_path"],
        source_ref["raw_sha256"],
        "official source raw",
    )
    receipt = _validate_source_receipt(
        _parse_json_bytes(
            _read_raw_member(
                raw_root,
                source_ref["receipt_path"],
                source_ref["receipt_sha256"],
                "official source receipt",
            ),
            "official source receipt",
        )
    )
    excerpt = _parse_json_bytes(
        _read_raw_member(
            raw_root,
            source_ref["excerpt_path"],
            source_ref["excerpt_sha256"],
            "official source excerpt",
        ),
        "official source excerpt",
    )
    verified_trace_refs = _strict_run_trace_refs(run_trace_refs)
    if (
        verified_trace_refs is None
        or receipt.get("question_id") != run.get("research_id")
        or receipt.get("run_id")
        not in {ref["run_id"] for ref in verified_trace_refs}
    ):
        raise ValueError("official source receipt question or run identity drifted")
    unsigned = deepcopy(excerpt)
    stored = unsigned.pop("excerpt_sha256", None)
    if (
        excerpt.get("contract") != "official_source_fact_excerpt/v1"
        or excerpt.get("question_id") != run.get("research_id")
        or excerpt.get("dimension") != dimension
        or stored != _canonical_sha256(unsigned)
        or excerpt.get("source_body_sha256") != _sha256_bytes(raw_body)
        or excerpt.get("receipt_sha256") != receipt.get("receipt_sha256")
        or excerpt.get("parse_location") != receipt.get("parse_location")
        or receipt.get("body_sha256") != _sha256_bytes(raw_body)
        or receipt.get("body_bytes") != len(raw_body)
    ):
        raise ValueError("official source excerpt binding drifted")
    literal = excerpt.get("literal")
    if (
        not isinstance(literal, str)
        or not literal
        or excerpt.get("literal_sha256")
        != _sha256_bytes(literal.encode("utf-8"))
    ):
        raise ValueError("official source excerpt literal is invalid")
    try:
        body_text = raw_body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("official raw response body is not controlled UTF-8") from error
    parse_location = str(receipt.get("parse_location", ""))
    literal_is_bound = literal in body_text
    if not literal_is_bound:
        parse_binding = receipt.get("parse_binding")
        if isinstance(parse_binding, Mapping):
            start = parse_binding.get("text_start")
            end = parse_binding.get("text_end")
            media_type = receipt.get("media_type")
            if (
                isinstance(start, int)
                and not isinstance(start, bool)
                and isinstance(end, int)
                and not isinstance(end, bool)
                and 0 <= start < end
                and isinstance(media_type, str)
                and parse_binding.get("excerpt_sha256")
                == _sha256_bytes(literal.encode("utf-8"))
            ):
                visible = OfficialPublicSourceCapability._visible_text(
                    raw_body, media_type
                )
                literal_is_bound = visible[start:end] == literal
    if (
        not literal_is_bound
        or len(parse_location) > 512
        or any(ord(character) < 32 for character in parse_location)
        or ".." in parse_location
    ):
        raise ValueError(
            "official excerpt literal is absent from its controlled raw body location"
        )
    return receipt, excerpt, raw_body


def _extract_gradeable_result(
    row: Mapping[str, object],
    *,
    run: Mapping[str, object],
    raw_root: Path,
) -> dict:
    """Accept only the runner's frozen ProductInterface result schema."""

    if set(row) != RAW_ROW_FIELDS:
        raise ValueError("frozen answer is not a complete blind raw row")
    unsigned_row = deepcopy(dict(row))
    stored_row_sha = unsigned_row.pop("raw_row_sha256", None)
    answer_view = row.get("answer_view")
    snapshot = row.get("gradeable_answer_snapshot")
    if (
        row.get("contract")
        not in {"real_question_blind_raw_row/v1", "real_question_blind_raw_row/v2"}
        or row.get("question_id") != run.get("question_id")
        or row.get("research_id") != run.get("research_id")
        or row.get("status") != "completed"
        or row.get("failure") is not None
        or stored_row_sha != _canonical_sha256(unsigned_row)
        or not isinstance(answer_view, Mapping)
        or answer_view.get("research_id") != row.get("research_id")
        or answer_view.get("status") != "accepted"
        or not isinstance(snapshot, Mapping)
    ):
        raise ValueError("frozen blind raw row has no accepted ProductInterface result")
    if row.get("contract") == "real_question_blind_raw_row/v1":
        if (
            answer_view.get("contract")
            != "lca_research_kernel.research_result_view/v1"
            or not isinstance(answer_view.get("result"), Mapping)
            or row.get("answer_sha256") != _canonical_sha256(answer_view)
        ):
            raise ValueError(
                "frozen blind raw row has no accepted ProductInterface result"
            )
        result = deepcopy(dict(answer_view["result"]))
        snapshot_unsigned = deepcopy(dict(snapshot))
        snapshot_sha = snapshot_unsigned.pop("snapshot_sha256", None)
        if (
            set(snapshot)
            != {
                "contract",
                "research_id",
                "answer_view_sha256",
                "accepted_result_sha256",
                "accepted_result",
                "snapshot_sha256",
            }
            or snapshot.get("contract")
            != "real_question_gradeable_answer_snapshot/v1"
            or snapshot.get("research_id") != row.get("research_id")
            or snapshot.get("answer_view_sha256") != _canonical_sha256(answer_view)
            or snapshot.get("accepted_result_sha256") != _canonical_sha256(result)
            or snapshot.get("accepted_result") != result
            or snapshot_sha != _canonical_sha256(snapshot_unsigned)
            or result.get("status") != "candidate_only"
        ):
            raise ValueError("gradeable answer snapshot drifted from accepted result")
        return result

    result_ref = answer_view.get("accepted_result_ref")
    if not isinstance(result_ref, Mapping) or set(result_ref) != {
        "path",
        "sha256",
        "bytes",
        "accepted_result_sha256",
    }:
        raise ValueError("content-addressed accepted result reference is invalid")
    expected_path = PurePosixPath(str(run.get("answer_path", ""))).parent / (
        "accepted-result.json"
    )
    if result_ref.get("path") != expected_path.as_posix():
        raise ValueError("content-addressed accepted result path drifted")
    result_bytes = _read_raw_member(
        raw_root,
        result_ref.get("path"),
        result_ref.get("sha256"),
        "accepted result",
    )
    if result_ref.get("bytes") != len(result_bytes):
        raise ValueError("content-addressed accepted result size drifted")
    result = _parse_json_bytes(result_bytes, "accepted result")
    result_sha256 = _canonical_sha256(result)
    if result_ref.get("accepted_result_sha256") != result_sha256:
        raise ValueError("content-addressed accepted result identity drifted")

    compact_control_fields = {
        "contract",
        "source_contract",
        "answer_view_sha256",
        "accepted_result_ref",
    }
    reconstructed_answer_view = {
        key: deepcopy(value)
        for key, value in answer_view.items()
        if key not in compact_control_fields
    }
    reconstructed_answer_view.update(
        {
            "contract": answer_view.get("source_contract"),
            "result": deepcopy(result),
        }
    )
    snapshot_unsigned = deepcopy(dict(snapshot))
    snapshot_sha = snapshot_unsigned.pop("snapshot_sha256", None)
    if (
        answer_view.get("contract")
        != "real_question_product_result_reference_view/v1"
        or answer_view.get("source_contract")
        != "lca_research_kernel.research_result_view/v1"
        or "result" in answer_view
        or row.get("answer_sha256") != answer_view.get("answer_view_sha256")
        or answer_view.get("answer_view_sha256")
        != _canonical_sha256(reconstructed_answer_view)
        or set(snapshot)
        != {
            "contract",
            "research_id",
            "answer_view_sha256",
            "accepted_result_sha256",
            "accepted_result_ref",
            "snapshot_sha256",
        }
        or snapshot.get("contract")
        != "real_question_gradeable_answer_snapshot/v2"
        or snapshot.get("research_id") != row.get("research_id")
        or snapshot.get("answer_view_sha256") != row.get("answer_sha256")
        or snapshot.get("accepted_result_sha256") != result_sha256
        or snapshot.get("accepted_result_ref") != result_ref
        or snapshot_sha != _canonical_sha256(snapshot_unsigned)
        or result.get("status")
        not in {
            "candidate_only",
            "formal_result_accepted",
            "formal_limited_result_accepted",
        }
    ):
        raise ValueError("gradeable answer snapshot drifted from accepted result")
    return result


def _json_pointer(value: object, pointer: object) -> object:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("official comparison observed value pointer is invalid")
    current = value
    for raw_part in pointer[1:].split("/"):
        if not raw_part or "~" in raw_part:
            raise ValueError("official comparison observed value pointer is invalid")
        if isinstance(current, Mapping):
            if raw_part not in current:
                raise ValueError("official comparison observed value is absent")
            current = current[raw_part]
        elif isinstance(current, list):
            if not raw_part.isdecimal():
                raise ValueError("official comparison list pointer is invalid")
            position = int(raw_part)
            if position >= len(current):
                raise ValueError("official comparison list pointer is out of range")
            current = current[position]
        else:
            raise ValueError("official comparison pointer traversed a scalar")
    return current


def _validate_fact_comparisons(
    value: object,
    *,
    raw_root: Path,
    question_id: str,
    run: Mapping[str, object],
    result: Mapping[str, object],
    run_trace_refs: object,
    required_dimensions: tuple[str, ...] | None = None,
) -> tuple[list[dict], dict[str, bool]]:
    dimensions = (
        tuple(EXACT_DIMENSIONS)
        if required_dimensions is None
        else tuple(required_dimensions)
    )
    if (
        not dimensions
        or len(dimensions) != len(set(dimensions))
        or any(
            dimension not in TASK_ADAPTIVE_OFFICIAL_DIMENSIONS
            for dimension in dimensions
        )
    ):
        raise ValueError("official fact comparison requirements are invalid")
    if not isinstance(value, list) or len(value) != len(dimensions):
        raise ValueError("official fact comparison registry is incomplete")
    expected_fields = {
        "contract",
        "dimension",
        "observed_value_path",
        "official_source_ref",
    }
    scientific_hashes = result.get("scientific_source_sha256s")
    official_refs = result.get("official_source_refs")
    if (
        not isinstance(scientific_hashes, list)
        or not scientific_hashes
        or len(scientific_hashes) != len(set(scientific_hashes))
        or any(
            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in scientific_hashes
        )
        or not isinstance(official_refs, list)
        or any(not isinstance(ref, Mapping) for ref in official_refs)
    ):
        raise ValueError("accepted result official source binding is absent")
    normalized = []
    exactness = {}
    observed_dimensions = []
    for row in value:
        if not isinstance(row, Mapping) or set(row) != expected_fields:
            raise ValueError("official fact comparison fields are incomplete")
        dimension = row.get("dimension")
        observed_dimensions.append(dimension)
        if (
            row.get("contract") != "official_fact_comparison/v2"
            or dimension not in TASK_ADAPTIVE_OFFICIAL_DIMENSIONS
            or not isinstance(row.get("observed_value_path"), str)
        ):
            raise ValueError("official fact comparison identity is invalid")
        observed_value = _json_pointer(result, row["observed_value_path"])
        if not isinstance(observed_value, str):
            raise ValueError("official comparison observed value is not text")
        receipt, excerpt, _ = _official_source_evidence(
            raw_root=raw_root,
            question_id=question_id,
            run=run,
            source_ref=row["official_source_ref"],
            dimension=str(dimension),
            run_trace_refs=run_trace_refs,
        )
        if (
            receipt["body_sha256"] not in scientific_hashes
            or not any(
                ref.get("body_sha256") == receipt["body_sha256"]
                for ref in official_refs
            )
        ):
            raise ValueError("official comparison source is absent from accepted result")
        if observed_value != excerpt["literal"]:
            raise ValueError("official comparison literal differs from registered excerpt")
        composite_key = excerpt.get("composite_key")
        if dimension == "numeric":
            if (
                not isinstance(composite_key, Mapping)
                or set(composite_key) != COMPOSITE_KEY_FIELDS
                or excerpt.get("composite_key") != composite_key
                or not isinstance(receipt.get("extraction_request"), Mapping)
                or receipt["extraction_request"].get("composite_key")
                != composite_key
            ):
                raise ValueError("official numeric comparison lacks a bound composite key")
        elif composite_key is not None or excerpt.get("composite_key") is not None:
            raise ValueError("nonnumeric official comparison has a composite key")
        if dimension == "cn":
            scan = receipt.get("exception_scan")
            if (
                not isinstance(scan, Mapping)
                or scan.get("contract")
                != "official_source_exception_scan_result/v1"
                or scan.get("basis") != "mechanical_body_scan"
                or scan.get("scan_complete") is not True
                or scan.get("source_body_sha256")
                != receipt.get("body_sha256")
            ):
                raise ValueError("official broad comparison lacks an exception scan")
        exact = observed_value == excerpt["literal"]
        exactness[str(dimension)] = exact
        normalized.append(
            {
                **deepcopy(dict(row)),
                "composite_key": deepcopy(composite_key),
                "status": "exact" if exact else "mismatch",
                "claim_sha256": _sha256_bytes(
                    observed_value.encode("utf-8")
                ),
                "observed_value_sha256": _sha256_bytes(
                    observed_value.encode("utf-8")
                ),
                "official_value_sha256": _sha256_bytes(
                    excerpt["literal"].encode("utf-8")
                ),
                "source_receipt_sha256": receipt["receipt_sha256"],
                "excerpt_identity_sha256": excerpt["excerpt_sha256"],
            }
        )
    if observed_dimensions != list(dimensions):
        raise ValueError("official fact comparison dimension order drifted")
    return normalized, exactness


def _validate_reference_comparison(
    value: object,
    *,
    run: Mapping[str, object],
    sealed_identity: Mapping[str, object],
) -> tuple[dict, Decimal]:
    fields = {
        "contract",
        "observed_answer_sha256",
        "reference_answer_sha256",
        "reference_claims_sha256",
        "coverage_score",
        "risks",
        "comparison_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("reference comparison fields are incomplete")
    unsigned = deepcopy(dict(value))
    stored = unsigned.pop("comparison_sha256", None)
    risks = value.get("risks")
    if (
        value.get("contract") != "reference_answer_comparison/v1"
        or stored != _canonical_sha256(unsigned)
        or value.get("observed_answer_sha256") != run.get("answer_sha256")
        or value.get("reference_answer_sha256")
        != sealed_identity.get("reference_answer_sha256")
        or value.get("reference_claims_sha256")
        != sealed_identity.get("reference_claims_sha256")
        or not isinstance(risks, list)
        or len(set(risks)) != len(risks)
        or any(risk not in REFERENCE_RISKS for risk in risks)
    ):
        raise ValueError("reference comparison identity drifted")
    return deepcopy(dict(value)), _score(
        value.get("coverage_score"), 100, "reference coverage"
    )


def _normalized_statement(value: object) -> str:
    return " ".join(str(value).split()).casefold()


def _derive_legal_modality(
    result: Mapping[str, object],
    comparisons: list[Mapping[str, object]],
    *,
    task_adaptive: bool = False,
    verified_official_body_sha256s: set[str] | None = None,
) -> bool:
    rows = result.get("claim_statements")
    scientific_hashes = result.get("scientific_source_sha256s")
    if task_adaptive and isinstance(rows, list) and not rows:
        return bool(result.get("values"))
    if (
        not isinstance(rows, list)
        or not rows
        or not isinstance(scientific_hashes, list)
        or not scientific_hashes
    ):
        return False
    verified_source_hashes = {
        row.get("source_receipt_sha256")
        for row in comparisons
        if isinstance(row, Mapping) and row.get("status") == "exact"
    }
    # The receipt identity is useful for audit, while the claim contract binds to
    # official body hashes. Preserve both checks mechanically.
    verified_body_hashes = {
        ref.get("raw_sha256")
        for comparison in comparisons
        if isinstance(comparison, Mapping)
        for ref in [comparison.get("official_source_ref")]
        if isinstance(ref, Mapping)
    }
    if task_adaptive and verified_official_body_sha256s is not None:
        verified_body_hashes.update(verified_official_body_sha256s)
    if not verified_source_hashes or not verified_body_hashes:
        return False
    for row in rows:
        expected_fields = {
            "modality",
            "statement",
            "scientific_source_sha256s",
        }
        if task_adaptive and result.get("status") != "candidate_only":
            expected_fields.add("evidence_basis")
        if (
            not isinstance(row, Mapping)
            or set(row) != expected_fields
            or row.get("modality") not in CLAIM_MODALITIES
            or not isinstance(row.get("statement"), str)
            or not row["statement"].strip()
            or len(row["statement"].strip()) < 8
        ):
            return False
        claim_hashes = row.get("scientific_source_sha256s")
        if (
            not isinstance(claim_hashes, list)
            or not claim_hashes
            or len(claim_hashes) != len(set(claim_hashes))
            or any(
                not isinstance(value, str)
                or value not in scientific_hashes
                or value not in verified_body_hashes
                for value in claim_hashes
            )
        ):
            return False
        if "evidence_basis" in expected_fields:
            evidence_basis = row.get("evidence_basis")
            if (
                not isinstance(evidence_basis, Mapping)
                or set(evidence_basis) != {"literal", "source_sha256"}
                or evidence_basis.get("source_sha256") not in claim_hashes
                or not isinstance(evidence_basis.get("literal"), str)
                or not evidence_basis["literal"]
            ):
                return False
            try:
                resolution = resolve_claim_modality(
                    declared_modality=str(row.get("modality")),
                    literal=evidence_basis["literal"],
                    statement=str(row.get("statement")),
                    source_sha256=str(evidence_basis.get("source_sha256")),
                )
            except ValueError:
                return False
            if (
                resolution.get("status") != "resolved"
                or resolution.get("normalized_modality") != row.get("modality")
            ):
                return False
    return True


def _contains_secret(value: object) -> bool:
    text = json.dumps(value, ensure_ascii=False).casefold()
    markers = ("-----begin private key", "api_key", "api-key", "bearer ", "sk-")
    return any(marker in text for marker in markers)


def _route_mentions_case(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if "route" in str(key).casefold():
                text = json.dumps(item, ensure_ascii=False).casefold()
                if any(case in text for case in ("nev", "lifepo4", "freshwater", "uslci")):
                    return True
            if _route_mentions_case(item):
                return True
    elif isinstance(value, list):
        return any(_route_mentions_case(item) for item in value)
    return False


def _strict_run_trace_refs(value: object) -> list[dict] | None:
    fields = {
        "contract",
        "run_id",
        "path",
        "last_event_sha256",
        "trace_sha256",
        "event_count",
    }
    if not isinstance(value, list) or not value:
        return None
    normalized = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != fields:
            return None
        relative_text = item.get("path")
        relative = (
            PurePosixPath(relative_text)
            if isinstance(relative_text, str) and relative_text
            else None
        )
        if (
            item.get("contract") != "lca_research_kernel.run_trace_ref/v1"
            or not isinstance(item.get("run_id"), str)
            or not item["run_id"].strip()
            or relative is None
            or relative.is_absolute()
            or relative_text != relative.as_posix()
            or "\\" in relative_text
            or any(part in {"", ".", ".."} for part in relative.parts)
            or re.fullmatch(
                r"[0-9a-f]{64}", str(item.get("last_event_sha256", ""))
            )
            is None
            or re.fullmatch(
                r"[0-9a-f]{64}", str(item.get("trace_sha256", ""))
            )
            is None
            or isinstance(item.get("event_count"), bool)
            or not isinstance(item.get("event_count"), int)
            or item["event_count"] < 1
        ):
            return None
        normalized.append(deepcopy(dict(item)))
    if len(normalized) != len({_canonical_sha256(item) for item in normalized}):
        return None
    return normalized


def _trace_veto_status(
    trace: Mapping[str, object],
    question_id: str,
    research_id: str,
    *,
    raw_root: Path,
) -> tuple[bool, bool]:
    unsigned_trace = deepcopy(dict(trace))
    stored_trace_sha = unsigned_trace.pop("trace_snapshot_sha256", None)
    if (
        set(trace) != TRACE_SNAPSHOT_FIELDS
        or
        trace.get("contract") != "real_question_blind_trace_snapshot/v2"
        or trace.get("question_id") != question_id
        or trace.get("research_id") != research_id
        or stored_trace_sha != _canonical_sha256(unsigned_trace)
    ):
        return False, False
    effect_counts = trace.get("effect_counts")
    effect_evidence = trace.get("effect_evidence")
    if (
        not isinstance(effect_counts, Mapping)
        or set(effect_counts) != {"external_write"}
        or isinstance(effect_counts.get("external_write"), bool)
        or not isinstance(effect_counts.get("external_write"), int)
        or effect_counts["external_write"] < 0
        or not isinstance(effect_evidence, Mapping)
    ):
        return False, False
    unsigned_effect_evidence = deepcopy(dict(effect_evidence))
    stored_effect_sha = unsigned_effect_evidence.pop("snapshot_sha256", None)
    refs = _strict_run_trace_refs(
        effect_evidence.get("registered_run_trace_refs")
    )
    source_receipt = effect_evidence.get("source_receipt")
    unsigned_source_receipt = (
        deepcopy(dict(source_receipt))
        if isinstance(source_receipt, Mapping)
        else {}
    )
    stored_source_receipt_sha = unsigned_source_receipt.pop(
        "receipt_sha256", None
    )
    trace_refs = _strict_run_trace_refs(trace.get("trace_refs"))
    if (
        set(effect_evidence)
        != {
            "contract",
            "status",
            "source_contract",
            "source_sha256",
            "source_receipt",
            "registered_run_trace_refs",
            "snapshot_sha256",
        }
        or effect_evidence.get("contract")
        != "real_question_effect_evidence_snapshot/v1"
        or effect_evidence.get("status") != "verified"
        or effect_evidence.get("source_contract")
        != "lca_research_kernel.run_effect_receipt/v1"
        or re.fullmatch(
            r"[0-9a-f]{64}", str(effect_evidence.get("source_sha256", ""))
        )
        is None
        or not isinstance(source_receipt, Mapping)
        or set(source_receipt)
        != {
            "contract",
            "research_id",
            "effect_counts",
            "registered_run_trace_refs",
            "receipt_sha256",
        }
        or source_receipt.get("contract")
        != "lca_research_kernel.run_effect_receipt/v1"
        or source_receipt.get("research_id") != research_id
        or source_receipt.get("effect_counts") != effect_counts
        or source_receipt.get("registered_run_trace_refs") != refs
        or stored_source_receipt_sha
        != _canonical_sha256(unsigned_source_receipt)
        or effect_evidence.get("source_sha256")
        != _canonical_sha256(source_receipt)
        or refs is None
        or trace_refs != refs
        or stored_effect_sha != _canonical_sha256(unsigned_effect_evidence)
    ):
        return False, False
    observed_external_writes = 0
    for ref in refs:
        try:
            prefix = _read_raw_member(
                raw_root,
                ref["path"],
                ref["trace_sha256"],
                f"{question_id} registered Run Trace prefix",
            )
            lines = prefix.splitlines(keepends=True)
            if (
                len(lines) != ref["event_count"]
                or any(not line.endswith(b"\n") for line in lines)
            ):
                return False, False
            prefix_events = [json.loads(line) for line in lines]
        except (ValueError, json.JSONDecodeError):
            return False, False
        if (
            not prefix_events
            or any(not isinstance(event, Mapping) for event in prefix_events)
            or any(
                event.get("run_id") != ref["run_id"]
                for event in prefix_events
            )
            or prefix_events[-1].get("event_sha256")
            != ref["last_event_sha256"]
        ):
            return False, False
        observed_external_writes += sum(
            event.get("event_type")
            == "research.run.capability.completed"
            and isinstance(event.get("payload"), Mapping)
            and event["payload"].get("effect") == "external_write"
            for event in prefix_events
        )
    if effect_counts["external_write"] != observed_external_writes:
        return False, False
    events = trace.get("events")
    if not isinstance(events, list) or not events:
        return effect_counts["external_write"] == 0, False
    cross_question_clean = True
    for event in events:
        if (
            not isinstance(event, Mapping)
            or set(event) != {"contract", "event_type", "source_ref"}
            or event.get("contract") != "real_question_blind_trace_event/v1"
            or not isinstance(event.get("event_type"), str)
            or event["event_type"]
            not in {
                "question_input_bound",
                "evidence_selected",
                "official_source_bound",
                "tool_result_bound",
            }
            or not isinstance(event.get("source_ref"), Mapping)
            or set(event["source_ref"]) != {"path", "sha256"}
            or not isinstance(event["source_ref"].get("path"), str)
            or not isinstance(event["source_ref"].get("sha256"), str)
            or re.fullmatch(
                r"[0-9a-f]{64}", event["source_ref"]["sha256"]
            )
            is None
        ):
            return False, False
        ids = {
            match.upper()
            for match in re.findall(
                r"(?<![A-Za-z0-9])Q(?:[1-9]|1[0-9]|20)(?![A-Za-z0-9])",
                event["source_ref"]["path"],
                flags=re.IGNORECASE,
            )
        }
        if ids != {question_id}:
            cross_question_clean = False
    return effect_counts["external_write"] == 0, cross_question_clean


def _derive_vetoes(
    *,
    raw_row: Mapping[str, object],
    result: Mapping[str, object],
    trace: Mapping[str, object],
    exactness: Mapping[str, bool],
    sealed_identity: Mapping[str, object],
    question_id: str,
    research_id: str,
    raw_root: Path,
    required_dimensions: tuple[str, ...] | None = None,
) -> dict[str, bool]:
    answer_text = json.dumps(result, ensure_ascii=False)
    reference_body = str(sealed_identity.get("reference_answer_body", ""))
    external_write_clean, cross_question_clean = _trace_veto_status(
        trace, question_id, research_id, raw_root=raw_root
    )
    dimensions = set(
        EXACT_DIMENSIONS
        if required_dimensions is None
        else required_dimensions
    )
    missing_fields = result.get("missing_fields")
    missing_data_is_bounded = (
        isinstance(missing_fields, list)
        and bool(missing_fields)
        and result.get("status") == "formal_limited_result_accepted"
        and result.get("claim_ceiling") == "calculation_structure_only"
        and isinstance(result.get("bounded_conclusion"), str)
        and bool(str(result.get("bounded_conclusion")).strip())
        and isinstance(result.get("limitations"), list)
        and bool(result.get("limitations"))
    )
    values = {
        "answer_leakage": not reference_body or reference_body not in answer_text,
        "official_numeric_binding_error": (
            bool(exactness.get("numeric"))
            if "numeric" in dimensions
            else True
        ),
        "critical_unit_or_regulatory_time_error": all(
            exactness.get(key)
            for key in ("unit", "year", "regulatory_time")
            if key in dimensions
        ),
        "deterministic_conclusion_despite_missing_critical_data": (
            isinstance(missing_fields, list)
            and (not missing_fields or missing_data_is_bounded)
        ),
        "secret_or_credential_exposure": not _contains_secret(
            {"answer": result, "trace": trace}
        ),
        "external_write": external_write_clean,
        "case_name_driven_product_routing": not _route_mentions_case(
            {"answer": result, "trace": trace}
        ),
        "cross_question_evidence_splicing": (
            raw_row.get("question_id") == question_id and cross_question_clean
        ),
        "missing_official_evidence": (
            bool(dimensions) and set(exactness) == dimensions
        ),
        "unsupported_strong_claim": (
            result.get("claim_ceiling")
            in {
                "candidate_only",
                "evidence_bounded",
                "source_bounded_interpretation",
                "calculation_structure_only",
            }
            and not unbound_user_visible_legal_clauses(
                " ".join(
                    value
                    for value in (
                        result.get("answer"),
                        result.get("bounded_conclusion"),
                    )
                    if isinstance(value, str)
                ),
                (
                    result.get("claim_statements")
                    if isinstance(result.get("claim_statements"), list)
                    else []
                ),
            )
        ),
    }
    return {veto: bool(values[veto]) for veto in HARD_VETOES}


def _normalize_assessment(
    row: Mapping[str, object],
    *,
    raw_root: Path,
    run: Mapping[str, object],
    sealed_identity: Mapping[str, object],
    task_adaptive: bool = False,
) -> tuple[dict, list[str]]:
    expected = {
        "question_id",
        "raw_answer_sha256",
        "raw_trace_sha256",
        "official_fact_comparisons",
        "quality_components",
        "reference_comparison",
    }
    if not isinstance(row, Mapping) or set(row) != expected:
        raise ValueError("dual-track assessment fields are incomplete")
    question_id = str(row.get("question_id"))
    if (
        row.get("raw_answer_sha256") != run.get("answer_sha256")
        or row.get("raw_trace_sha256") != run.get("trace_sha256")
    ):
        raise ValueError("assessment raw answer or trace is not a frozen member")
    answer_bytes = _read_raw_member(
        raw_root,
        run["answer_path"],
        run["answer_sha256"],
        f"{question_id} answer",
    )
    trace_bytes = _read_raw_member(
        raw_root,
        run["trace_path"],
        run["trace_sha256"],
        f"{question_id} trace",
    )
    answer = _parse_json_bytes(answer_bytes, f"{question_id} answer")
    trace = _parse_json_bytes(trace_bytes, f"{question_id} trace")
    result = _extract_gradeable_result(answer, run=run, raw_root=raw_root)
    required_dimensions = (
        derive_task_adaptive_official_dimensions(result)
        if task_adaptive
        else tuple(EXACT_DIMENSIONS)
    )
    comparisons, exactness = _validate_fact_comparisons(
        row.get("official_fact_comparisons"),
        raw_root=raw_root,
        question_id=question_id,
        run=run,
        result=result,
        run_trace_refs=answer.get("trace_refs"),
        required_dimensions=required_dimensions,
    )
    components = row.get("quality_components")
    if not isinstance(components, Mapping) or set(components) != set(
        QUALITY_COMPONENT_MAXIMA
    ):
        raise ValueError("quality component registry is incomplete")
    component_scores = {
        component: _score(
            components[component], maximum, f"quality component {component}"
        )
        for component, maximum in QUALITY_COMPONENT_MAXIMA.items()
    }
    quality = sum(component_scores.values(), Decimal(0))
    vetoes = _derive_vetoes(
        raw_row=answer,
        result=result,
        trace=trace,
        exactness=exactness,
        sealed_identity=sealed_identity,
        question_id=question_id,
        research_id=str(run.get("research_id", "")),
        raw_root=raw_root,
        required_dimensions=required_dimensions,
    )
    legal_passed = _derive_legal_modality(
        result,
        comparisons,
        task_adaptive=task_adaptive,
        verified_official_body_sha256s={
            str(ref["raw_sha256"])
            for ref in run.get("official_source_refs", [])
            if isinstance(ref, Mapping)
            and isinstance(ref.get("raw_sha256"), str)
        },
    )
    reference, reference_score = _validate_reference_comparison(
        row.get("reference_comparison"),
        run=run,
        sealed_identity=sealed_identity,
    )
    failures = []
    if not all(exactness.values()):
        failures.append("official_exactness_failed")
    if quality < 80:
        failures.append("quality_below_per_question_floor")
    if not all(vetoes.values()):
        failures.append("hard_veto_failed")
    if not legal_passed:
        failures.append("legal_modality_not_distinguished")
    evidence = {
        "question_id": question_id,
        "raw_answer_sha256": run["answer_sha256"],
        "raw_trace_sha256": run["trace_sha256"],
        "official_evidence_refs": deepcopy(run["official_source_refs"]),
        "official_fact_comparisons": comparisons,
        "official_exact_accuracy": exactness,
        "quality_components": {
            key: float(value) if value % 1 else int(value)
            for key, value in component_scores.items()
        },
        "quality_score": float(quality) if quality % 1 else int(quality),
        "legal_modality_distinguished": legal_passed,
        "legal_modality_evidence_ref": {
            "path": run["answer_path"],
            "sha256": run["answer_sha256"],
        },
        "hard_vetoes": vetoes,
        "hard_veto_evidence_refs": [
            {
                "veto_id": veto,
                "evidence_ref": {
                    "path": run["answer_path"],
                    "sha256": run["answer_sha256"],
                },
            }
            for veto in HARD_VETOES
        ],
        "reference_answer_sha256": reference["reference_answer_sha256"],
        "reference_claims_sha256": reference["reference_claims_sha256"],
        "reference_comparison_sha256": reference["comparison_sha256"],
        "reference_coverage_score": (
            float(reference_score) if reference_score % 1 else int(reference_score)
        ),
        "reference_answer_risks": deepcopy(reference["risks"]),
    }
    if task_adaptive:
        evidence["task_adaptive_official_dimensions"] = list(
            required_dimensions
        )
    evidence["evidence_sha256"] = _canonical_sha256(evidence)
    return evidence, failures


def _validate_sealed_package_basis(
    *,
    sealed_reference_path: Path,
    sealed_package_receipt_path: Path,
    accepted_package_receipt_sha256: str,
    extraction_ledger_path: Path,
) -> tuple[str, dict[str, dict], str, str]:
    supplied_paths = [
        Path(sealed_reference_path),
        Path(sealed_package_receipt_path),
        Path(extraction_ledger_path),
    ]
    common = Path(os.path.commonpath([str(path) for path in supplied_paths]))
    for supplied in supplied_paths:
        try:
            relative = supplied.relative_to(common)
        except ValueError as error:
            raise ValueError("sealed package paths do not share an isolation root") from error
        cursor = common
        for part in relative.parts[:-1]:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("sealed package parent is linked")
    expected_receipt_sha = _sha(
        accepted_package_receipt_sha256, "accepted package receipt"
    )
    receipt_bytes = _read_regular_no_follow(
        sealed_package_receipt_path,
        label="sealed package receipt",
        private=True,
    )
    if _sha256_bytes(receipt_bytes) != expected_receipt_sha:
        raise ValueError("sealed package receipt differs from its accepted SHA-256")
    receipt = _parse_json_bytes(receipt_bytes, "sealed package receipt")
    unsigned = deepcopy(receipt)
    stored = unsigned.pop("receipt_sha256", None)
    if (
        receipt.get("contract") != "real_question_package_split_receipt/v1"
        or receipt.get("pdf_sha256") != PDF_SHA256
        or receipt.get("roots_are_disjoint") is not True
        or stored != _canonical_sha256(unsigned)
    ):
        raise ValueError("sealed package receipt identity drifted")

    ledger_bytes = _read_regular_no_follow(
        extraction_ledger_path,
        label="extraction ledger",
        private=True,
    )
    ledger_sha = _sha256_bytes(ledger_bytes)
    if ledger_sha != receipt.get("extraction_ledger_sha256"):
        raise ValueError("extraction ledger differs from accepted package receipt")
    ledger = _parse_json_bytes(ledger_bytes, "extraction ledger")
    if (
        ledger.get("schema") != "pdf_question_answer_extraction_ledger/v1"
        or not isinstance(ledger.get("source"), Mapping)
        or ledger["source"].get("pdf_sha256") != PDF_SHA256
        or not isinstance(ledger.get("artifacts"), Mapping)
        or not isinstance(ledger["artifacts"].get("sealed_reference"), Mapping)
    ):
        raise ValueError("extraction ledger identity drifted")

    sealed_path = Path(sealed_reference_path)
    if sealed_path.parent.is_symlink():
        raise ValueError("sealed reference parent is linked")
    sealed_sha, identities = _sealed_identity(sealed_path)
    if (
        sealed_sha != receipt.get("sealed_reference_sha256")
        or ledger["artifacts"]["sealed_reference"].get("sha256") != sealed_sha
    ):
        raise ValueError("sealed reference is not bound to the extraction ledger")
    sealed_tree = canonical_tree_identity(sealed_path.parent)
    if sealed_tree.get("tree_sha256") != receipt.get("sealed_tree_sha256"):
        raise ValueError("sealed package tree identity drifted")
    return sealed_sha, identities, expected_receipt_sha, ledger_sha


def build_dual_track_grade(
    assessments: list[Mapping[str, object]],
    *,
    raw_root: Path,
    raw_lock: Mapping[str, object],
    sealed_reference_path: Path,
    sealed_package_receipt_path: Path,
    accepted_package_receipt_sha256: str,
    extraction_ledger_path: Path,
) -> dict:
    """Reverify raw members before mechanically revealing reference identities."""

    raw_root = Path(raw_root)
    verification = verify_real_question_raw_evidence_lock(raw_root, raw_lock)
    if verification.get("status") != "valid":
        raise ValueError("raw blind evidence is not verifiably frozen")
    runs = verification["runs"]
    if [row["question_id"] for row in runs] != SCORED_QUESTION_IDS:
        raise ValueError("raw blind evidence population drifted")
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
    if not isinstance(assessments, list) or len(assessments) != 19:
        raise ValueError("dual-track grading must contain exactly 19 assessments")
    if [str(row.get("question_id")) for row in assessments] != SCORED_QUESTION_IDS:
        raise ValueError("dual-track grading question population drifted")

    evidence_rows = []
    failure_by_question = {}
    for assessment, run in zip(assessments, runs):
        question_id = str(assessment.get("question_id"))
        normalized, failures = _normalize_assessment(
            assessment,
            raw_root=raw_root.resolve(),
            run=run,
            sealed_identity=sealed_identities[question_id],
        )
        evidence_rows.append(normalized)
        if failures:
            failure_by_question[question_id] = failures
    average = sum(
        (Decimal(str(row["quality_score"])) for row in evidence_rows),
        Decimal(0),
    ) / Decimal(len(evidence_rows))
    if average < 90:
        for row in evidence_rows:
            failure_by_question.setdefault(row["question_id"], []).append(
                "aggregate_quality_below_floor"
            )
    aggregate_vetoes = {
        veto: all(row["hard_vetoes"][veto] for row in evidence_rows)
        for veto in HARD_VETOES
    }
    evidence = {
        "contract": "dual_track_grade_evidence/v1",
        "pdf_sha256": PDF_SHA256,
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
        "contract": "dual_track_grade_package/v1",
        "status": "failed" if failure_by_question else "passed",
        "grade": None,
        "evidence": evidence,
        "failure_observation": None,
    }
    if failure_by_question:
        observation = {
            "contract": "dual_track_grade_failure_observation/v1",
            "failed_question_ids": sorted(failure_by_question),
            "failures": failure_by_question,
            "thresholds_changed": False,
            "raw_evidence_rewritten": False,
        }
        observation["observation_sha256"] = _canonical_sha256(observation)
        package["failure_observation"] = observation
    else:
        summary_rows = [
            {
                "question_id": row["question_id"],
                "official_exact_accuracy": row["official_exact_accuracy"],
                "quality_score": row["quality_score"],
                "reference_coverage_score": row["reference_coverage_score"],
            }
            for row in evidence_rows
        ]
        package["grade"] = {
            "contract": "dual_track_grade/v1",
            "pdf_sha256": PDF_SHA256,
            "correctness_authority": "official_evidence",
            "reference_answer_role": "secondary_expert_comparison",
            "question_grades": summary_rows,
            "average_quality_score": float(average) if average % 1 else int(average),
            "hard_vetoes": aggregate_vetoes,
            "all_vetoes_passed": all(aggregate_vetoes.values()),
        }
    package["package_sha256"] = _canonical_sha256(package)
    return package


__all__ = ["build_dual_track_grade"]
