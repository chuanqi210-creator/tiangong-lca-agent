"""Thin, answer-isolated runner for the frozen real-question baseline."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

from .real_question_contracts import (
    PDF_SHA256,
    QUESTION_DISPOSITIONS,
    QUESTION_IDS,
    SCORED_QUESTION_IDS,
)
from .real_question_pack import (
    _read_regular_no_follow,
    _reject_root_symlink,
    validate_blind_execution_isolation,
    validate_question_package_tree,
)


RAW_ROW_CONTRACT = "real_question_blind_raw_row/v1"
LARGE_RESULT_RAW_ROW_CONTRACT = "real_question_blind_raw_row/v2"
PROGRESS_INDEX_CONTRACT = "real_question_blind_progress_index/v1"
FINAL_INDEX_CONTRACT = "real_question_raw_run_index/v1"
_AUTOMATIC_RESUME_BOUNDARIES = {
    "candidate_preview_available",
    "status_observation_only",
}
_MAX_AUTOMATIC_RESUMES = 2
_FORBIDDEN_OUTPUT_MARKERS = (
    "sealed",
    "reference-answer",
    "oracle",
    "grader",
)
_MANIFEST_FIELDS = {
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
}
_ROW_FIELDS = {
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
_TRACE_FIELDS = {
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


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    content = _canonical_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return deepcopy(dict(value))


def _checked_sha(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} SHA-256 is invalid")
    return value


def _deduplicated_refs(
    payloads: Sequence[Mapping[str, object] | None],
    key: str,
) -> list[dict]:
    refs: list[dict] = []
    observed: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        candidate = payload.get(key)
        if not isinstance(candidate, Mapping):
            continue
        normalized = deepcopy(dict(candidate))
        identity = _canonical_sha256(normalized)
        if identity not in observed:
            refs.append(normalized)
            observed.add(identity)
    return refs


def _deduplicated_timings(
    payloads: Sequence[Mapping[str, object] | None],
) -> list[dict]:
    receipts: list[dict] = []
    observed: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        candidate = payload.get("answer_timing_receipt")
        if not isinstance(candidate, Mapping):
            continue
        normalized = deepcopy(dict(candidate))
        identity = _canonical_sha256(normalized)
        if identity not in observed:
            receipts.append(normalized)
            observed.add(identity)
    return receipts


def _official_source_refs(answer_view: object) -> list[dict]:
    if not isinstance(answer_view, Mapping):
        return []
    result = answer_view.get("result")
    if not isinstance(result, Mapping):
        return []
    candidates = result.get("official_source_refs", [])
    if candidates is None:
        return []
    if not isinstance(candidates, list) or any(
        not isinstance(row, Mapping) for row in candidates
    ):
        raise ValueError("blind-run official source references are invalid")
    refs: list[dict] = []
    observed: set[str] = set()
    for candidate in candidates:
        normalized = deepcopy(dict(candidate))
        identity = _canonical_sha256(normalized)
        if identity not in observed:
            refs.append(normalized)
            observed.add(identity)
    return refs


def _gradeable_answer_snapshot(
    answer_view: object,
    *,
    research_id: str,
) -> dict | None:
    """Freeze exactly the accepted result, never a parallel grader answer schema."""

    if not isinstance(answer_view, Mapping):
        return None
    result = answer_view.get("result")
    if (
        answer_view.get("contract")
        != "lca_research_kernel.research_result_view/v1"
        or answer_view.get("research_id") != research_id
        or answer_view.get("status") != "accepted"
        or not isinstance(result, Mapping)
    ):
        return None
    snapshot = {
        "contract": "real_question_gradeable_answer_snapshot/v1",
        "research_id": research_id,
        "answer_view_sha256": _canonical_sha256(answer_view),
        "accepted_result_sha256": _canonical_sha256(result),
        "accepted_result": deepcopy(dict(result)),
    }
    snapshot["snapshot_sha256"] = _canonical_sha256(snapshot)
    return snapshot


def _content_address_large_accepted_result(
    row: Mapping[str, object],
    *,
    question_id: str,
) -> tuple[dict, dict[str, bytes]]:
    """Move one accepted result out of an oversized row without changing it."""

    compact = deepcopy(dict(row))
    answer_view = compact.get("answer_view")
    snapshot = compact.get("gradeable_answer_snapshot")
    if (
        compact.get("status") != "completed"
        or compact.get("failure") is not None
        or not isinstance(answer_view, Mapping)
        or answer_view.get("contract")
        != "lca_research_kernel.research_result_view/v1"
        or answer_view.get("status") != "accepted"
        or not isinstance(answer_view.get("result"), Mapping)
        or not isinstance(snapshot, Mapping)
        or snapshot.get("contract")
        != "real_question_gradeable_answer_snapshot/v1"
    ):
        raise ValueError("only an accepted result may use content-addressed packaging")
    result = deepcopy(dict(answer_view["result"]))
    result_content = _canonical_bytes(result) + b"\n"
    result_ref = {
        "path": f"runs/{question_id}/accepted-result.json",
        "sha256": hashlib.sha256(result_content).hexdigest(),
        "bytes": len(result_content),
        "accepted_result_sha256": _canonical_sha256(result),
    }
    answer_view_sha256 = _canonical_sha256(answer_view)
    compact_answer_view = {
        key: deepcopy(value)
        for key, value in answer_view.items()
        if key not in {"contract", "result"}
    }
    compact_answer_view.update(
        {
            "contract": "real_question_product_result_reference_view/v1",
            "source_contract": "lca_research_kernel.research_result_view/v1",
            "answer_view_sha256": answer_view_sha256,
            "accepted_result_ref": deepcopy(result_ref),
        }
    )
    compact_snapshot = {
        "contract": "real_question_gradeable_answer_snapshot/v2",
        "research_id": compact["research_id"],
        "answer_view_sha256": answer_view_sha256,
        "accepted_result_sha256": _canonical_sha256(result),
        "accepted_result_ref": deepcopy(result_ref),
    }
    compact_snapshot["snapshot_sha256"] = _canonical_sha256(compact_snapshot)
    compact.update(
        {
            "contract": LARGE_RESULT_RAW_ROW_CONTRACT,
            "answer_view": compact_answer_view,
            "answer_sha256": answer_view_sha256,
            "gradeable_answer_snapshot": compact_snapshot,
        }
    )
    return compact, {"accepted-result.json": result_content}


def _validated_run_trace_ref(value: object) -> dict:
    fields = {
        "contract",
        "run_id",
        "path",
        "last_event_sha256",
        "trace_sha256",
        "event_count",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("registered Run Trace reference is incomplete")
    ref = deepcopy(dict(value))
    relative_text = ref.get("path")
    relative = (
        PurePosixPath(relative_text)
        if isinstance(relative_text, str) and relative_text
        else None
    )
    if (
        ref.get("contract") != "lca_research_kernel.run_trace_ref/v1"
        or not isinstance(ref.get("run_id"), str)
        or not ref["run_id"].strip()
        or relative is None
        or relative.is_absolute()
        or relative_text != relative.as_posix()
        or "\\" in relative_text
        or any(part in {"", ".", ".."} for part in relative.parts)
        or not isinstance(ref.get("last_event_sha256"), str)
        or len(ref["last_event_sha256"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in ref["last_event_sha256"]
        )
        or not isinstance(ref.get("trace_sha256"), str)
        or len(ref["trace_sha256"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in ref["trace_sha256"]
        )
        or isinstance(ref.get("event_count"), bool)
        or not isinstance(ref.get("event_count"), int)
        or ref["event_count"] < 1
    ):
        raise ValueError("registered Run Trace reference is invalid")
    return ref


def _effect_evidence_snapshot(
    payloads: Sequence[Mapping[str, object] | None],
    *,
    research_id: str,
) -> tuple[dict[str, int | None], dict, list[dict]]:
    """Use a registered Product Interface receipt; unknown is explicitly fail-closed."""

    receipts: list[dict] = []
    returned_trace_refs: list[dict] = []
    paired_view_count = 0
    unpaired_view_count = 0
    for payload in payloads:
        if not isinstance(payload, Mapping):
            if paired_view_count:
                raise ValueError(
                    "Product Interface effect evidence is missing after an earlier view"
                )
            continue
        if payload.get("research_id") != research_id:
            raise ValueError("Product Interface returned a foreign research identity")
        has_returned_ref = "run_trace_ref" in payload
        has_effect_receipt = "effect_receipt" in payload
        if has_returned_ref != has_effect_receipt:
            raise ValueError(
                "Product Interface trace and effect evidence must be paired"
            )
        if not has_returned_ref:
            unpaired_view_count += 1
            continue
        paired_view_count += 1
        returned_ref = _validated_run_trace_ref(payload["run_trace_ref"])
        returned_trace_refs.append(returned_ref)
        candidate = payload.get("effect_receipt")
        if not isinstance(candidate, Mapping):
            raise ValueError("Product Interface effect receipt is absent")
        receipt = deepcopy(dict(candidate))
        unsigned_receipt = deepcopy(receipt)
        stored_receipt_sha = unsigned_receipt.pop("receipt_sha256", None)
        effect_counts = receipt.get("effect_counts")
        refs = receipt.get("registered_run_trace_refs")
        if (
            set(receipt)
            != {
                "contract",
                "research_id",
                "effect_counts",
                "registered_run_trace_refs",
                "receipt_sha256",
            }
            or
            receipt.get("contract")
            != "lca_research_kernel.run_effect_receipt/v1"
            or receipt.get("research_id") != research_id
            or stored_receipt_sha != _canonical_sha256(unsigned_receipt)
            or not isinstance(effect_counts, Mapping)
            or set(effect_counts) != {"external_write"}
            or isinstance(effect_counts.get("external_write"), bool)
            or not isinstance(effect_counts.get("external_write"), int)
            or effect_counts["external_write"] < 0
            or not isinstance(refs, list)
            or not refs
        ):
            raise ValueError("Product Interface effect receipt is invalid")
        normalized_refs = [_validated_run_trace_ref(ref) for ref in refs]
        normalized_ref_identities = {
            _canonical_sha256(ref) for ref in normalized_refs
        }
        if (
            len(normalized_refs)
            != len(normalized_ref_identities)
            or _canonical_sha256(returned_ref)
            not in normalized_ref_identities
        ):
            raise ValueError(
                "effect receipt Run Trace references are unregistered or mismatched"
            )
        receipt["registered_run_trace_refs"] = normalized_refs
        run_ids = [ref["run_id"] for ref in normalized_refs]
        paths = [ref["path"] for ref in normalized_refs]
        if len(run_ids) != len(set(run_ids)) or len(paths) != len(set(paths)):
            raise ValueError("effect receipt Run Trace identities are ambiguous")
        receipts.append(receipt)
    if paired_view_count and unpaired_view_count:
        raise ValueError("Product Interface effect evidence disappeared between views")
    if not receipts:
        evidence = {
            "contract": "real_question_effect_evidence_snapshot/v1",
            "status": "missing_fail_closed",
            "source_contract": None,
            "source_sha256": None,
            "source_receipt": None,
            "registered_run_trace_refs": [],
        }
        evidence["snapshot_sha256"] = _canonical_sha256(evidence)
        return {"external_write": None}, evidence, []
    previous = receipts[0]
    previous_returned_ref = returned_trace_refs[0]
    for index, receipt in enumerate(receipts[1:], start=1):
        returned_ref = returned_trace_refs[index]
        if (
            returned_ref["run_id"] != previous_returned_ref["run_id"]
            or returned_ref["path"] != previous_returned_ref["path"]
        ):
            raise ValueError("Product Interface current Run Trace identity switched")
        if returned_ref["event_count"] < previous_returned_ref["event_count"]:
            raise ValueError("Product Interface current Run Trace rolled back")
        if (
            returned_ref["event_count"] == previous_returned_ref["event_count"]
            and returned_ref != previous_returned_ref
        ):
            raise ValueError("Product Interface current Run Trace was rewritten")
        previous_refs = {
            ref["run_id"]: ref
            for ref in previous["registered_run_trace_refs"]
        }
        current_refs = {
            ref["run_id"]: ref
            for ref in receipt["registered_run_trace_refs"]
        }
        if not set(previous_refs).issubset(current_refs):
            raise ValueError("effect receipt Run Trace set rolled back")
        for run_id, old in previous_refs.items():
            new = current_refs[run_id]
            if old["path"] != new["path"]:
                raise ValueError("effect receipt Run Trace path drifted")
            if new["event_count"] < old["event_count"]:
                raise ValueError("effect receipt Run Trace prefix rolled back")
            if new["event_count"] == old["event_count"] and new != old:
                raise ValueError("effect receipt Run Trace identity was rewritten")
        if (
            receipt["effect_counts"]["external_write"]
            < previous["effect_counts"]["external_write"]
        ):
            raise ValueError("effect receipt external-write count rolled back")
        previous = receipt
        previous_returned_ref = returned_ref
    receipt = receipts[-1]
    evidence = {
        "contract": "real_question_effect_evidence_snapshot/v1",
        "status": "verified",
        "source_contract": receipt["contract"],
        "source_sha256": _canonical_sha256(receipt),
        "source_receipt": deepcopy(receipt),
        "registered_run_trace_refs": deepcopy(
            receipt["registered_run_trace_refs"]
        ),
    }
    evidence["snapshot_sha256"] = _canonical_sha256(evidence)
    return deepcopy(dict(receipt["effect_counts"])), evidence, receipts


class RealQuestionBlindRunner:
    """Drive question-only inputs through the sole Product Interface."""

    def __init__(
        self,
        *,
        product_interface: object,
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
        clock: Callable[[], str],
    ):
        for supplied, label in (
            (Path(question_root), "question root"),
            (Path(output_root), "output root"),
            (Path(sealed_root), "sealed root"),
            *(
                ((Path(official_source_cache_root), "official source cache root"),)
                if official_source_cache_root is not None
                else ()
            ),
        ):
            _reject_root_symlink(supplied, f"blind-run {label}")
        self.product_interface = product_interface
        self.question_root = Path(question_root).resolve()
        self.manifest = deepcopy(dict(blind_manifest))
        self.execution_environment = execution_environment
        self.authorization = deepcopy(dict(question_read_authorization_receipt))
        self.output_root = Path(output_root).resolve()
        self.sealed_root = Path(sealed_root).resolve()
        self.sealed_identifiers = set(sealed_identifiers)
        self.official_source_cache_root = (
            None
            if official_source_cache_root is None
            else Path(official_source_cache_root).resolve()
        )
        self.sealed_package_receipt_path = _reject_root_symlink(
            Path(sealed_package_receipt_path),
            "blind-run sealed package receipt",
        )
        self.accepted_package_receipt_sha256 = _checked_sha(
            accepted_package_receipt_sha256,
            "accepted package receipt",
        )
        self.extraction_ledger_path = _reject_root_symlink(
            Path(extraction_ledger_path), "blind-run extraction ledger"
        )
        self._questions: dict[str, dict] = {}
        self.clock = clock

    def run(self, *, question_ids: Sequence[str] | None = None) -> dict:
        selected = self._validate_selection(question_ids)
        pack, verification = self._validate_inputs()
        questions = {row["question_id"]: row for row in pack["questions"]}
        self._questions = deepcopy(questions)
        self._prepare_output_root()
        for question_id in selected:
            self._run_one(questions[question_id], verification)
        return self._write_indexes(selected)

    def _validate_selection(
        self, question_ids: Sequence[str] | None
    ) -> list[str]:
        selected = (
            list(SCORED_QUESTION_IDS)
            if question_ids is None
            else list(question_ids)
        )
        if (
            not selected
            or len(selected) != len(set(selected))
            or any(question_id not in SCORED_QUESTION_IDS for question_id in selected)
        ):
            raise ValueError("blind-run question selection is invalid")
        if selected != [
            question_id for question_id in SCORED_QUESTION_IDS if question_id in selected
        ]:
            raise ValueError("blind-run question order drifted")
        return selected

    def _validate_inputs(self) -> tuple[dict, dict]:
        if (
            set(self.manifest) != _MANIFEST_FIELDS
            or self.manifest.get("contract") != "blind_run_manifest/v1"
            or self.manifest.get("pdf_sha256") != PDF_SHA256
            or self.manifest.get("question_ids") != SCORED_QUESTION_IDS
            or self.manifest.get("question_dispositions") != QUESTION_DISPOSITIONS
            or self.manifest.get("sealed_reference_access") != "forbidden"
        ):
            raise ValueError("blind-run manifest is invalid")
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
            _checked_sha(self.manifest.get(field), f"blind-run {field}")
        if not isinstance(self.manifest.get("model_id"), str) or not self.manifest[
            "model_id"
        ].strip():
            raise ValueError("blind-run model identity is absent")

        identity = getattr(self.execution_environment, "identity", None)
        policy_sha256 = getattr(self.execution_environment, "policy_sha256", None)
        if (
            not isinstance(identity, Mapping)
            or self.manifest["execution_identity_sha256"]
            != _canonical_sha256(identity)
            or self.manifest["permission_policy_sha256"] != policy_sha256
        ):
            raise ValueError("blind-run execution identity drifted")
        authoritative = self.execution_environment.authorize_file(
            self.question_root / "real-question-pack.json",
            "read",
        )
        if authoritative != self.authorization:
            raise ValueError("blind-run question authorization drifted")
        authorization_body = deepcopy(self.authorization)
        authorization_sha256 = authorization_body.pop("receipt_sha256", None)
        if (
            authorization_body.get("contract")
            != "lca_research_kernel.execution_authorization/v1"
            or authorization_body.get("status") != "authorized"
            or authorization_body.get("kind") != "file"
            or authorization_body.get("execution_id") != identity.get("execution_id")
            or authorization_body.get("policy_sha256") != policy_sha256
            or not isinstance(authorization_body.get("payload"), Mapping)
            or authorization_body["payload"].get("operation") != "read"
            or Path(str(authorization_body["payload"].get("path"))).resolve()
            != self.question_root / "real-question-pack.json"
            or authorization_sha256 != _canonical_sha256(authorization_body)
            or self.manifest["question_read_authorization_sha256"]
            != authorization_sha256
        ):
            raise ValueError("blind-run question authorization receipt drifted")

        question_tree = validate_question_package_tree(
            self.question_root,
            expected_tree_sha256=self.manifest["question_tree_sha256"],
        )
        isolation = validate_blind_execution_isolation(
            self.manifest,
            execution_environment=self.execution_environment,
            question_root=self.question_root,
            sealed_root=self.sealed_root,
            question_read_authorization_receipt=self.authorization,
            sealed_identifiers=self.sealed_identifiers,
            sealed_package_receipt_path=self.sealed_package_receipt_path,
            accepted_package_receipt_sha256=(
                self.accepted_package_receipt_sha256
            ),
            extraction_ledger_path=self.extraction_ledger_path,
            question_tree_verification=question_tree,
        )
        if (
            isolation["question_pack_sha256"]
            != self.manifest["question_pack_sha256"]
        ):
            raise ValueError("blind-run question pack identity drifted")
        pack = deepcopy(isolation["question_pack"])
        if (
            pack.get("contract") != "real_question_pack/v1"
            or pack.get("pdf_sha256") != PDF_SHA256
            or pack.get("scored_question_ids") != SCORED_QUESTION_IDS
            or not isinstance(pack.get("questions"), list)
            or [row.get("question_id") for row in pack["questions"]]
            != QUESTION_IDS
        ):
            raise ValueError("blind-run question population drifted")
        for row in pack["questions"]:
            if (
                not isinstance(row, Mapping)
                or set(row)
                != {
                    "question_id",
                    "disposition",
                    "question_body",
                    "page_number",
                    "image_refs",
                }
                or row.get("disposition")
                != QUESTION_DISPOSITIONS.get(row.get("question_id"))
                or not isinstance(row.get("question_body"), str)
                or not row["question_body"].strip()
                or not isinstance(row.get("image_refs"), list)
            ):
                raise ValueError("blind-run question row is invalid")
        return pack, isolation

    def _prepare_output_root(self) -> None:
        if self.output_root.is_symlink():
            raise ValueError("blind-run output root cannot be a symbolic link")
        for relative in (
            Path("blind-run-progress-index.json"),
            Path("run-index.json"),
            Path("runs") / "Q1" / "answer.json",
            Path("runs") / "Q1" / "trace.json",
        ):
            if any(
                marker in relative.as_posix().casefold()
                for marker in _FORBIDDEN_OUTPUT_MARKERS
            ):
                raise ValueError("blind-run output path contains a forbidden marker")
            self.execution_environment.authorize_file(
                self.output_root / relative,
                "write",
            )
        self.output_root.mkdir(parents=True, exist_ok=True)
        (self.output_root / "runs").mkdir(exist_ok=True)

    def _input_basis(self, question: Mapping[str, object]) -> dict:
        return {
            "blind_manifest_sha256": _canonical_sha256(self.manifest),
            "question_sha256": _canonical_sha256(question),
            "question_tree_sha256": self.manifest["question_tree_sha256"],
            "execution_identity_sha256": self.manifest[
                "execution_identity_sha256"
            ],
            "question_read_authorization_sha256": self.manifest[
                "question_read_authorization_sha256"
            ],
            "accepted_package_receipt_sha256": (
                self.accepted_package_receipt_sha256
            ),
        }

    def _row_path(self, question_id: str) -> Path:
        path = self.output_root / "runs" / question_id / "answer.json"
        self.execution_environment.authorize_file(path, "write")
        return path

    def _trace_path(self, question_id: str) -> Path:
        path = self.output_root / "runs" / question_id / "trace.json"
        self.execution_environment.authorize_file(path, "write")
        return path

    def _load_frozen_row(
        self,
        path: Path,
        *,
        question_id: str,
        input_basis_sha256: str,
    ) -> dict | None:
        if not path.exists():
            return None
        if path.is_symlink():
            raise ValueError("blind-run frozen row cannot be a symbolic link")
        row = _load_json(path, "blind-run frozen row")
        stored = row.pop("raw_row_sha256", None)
        if (
            set(row) != _ROW_FIELDS - {"raw_row_sha256"}
            or stored != _canonical_sha256(row)
            or row.get("question_id") != question_id
            or row.get("input_basis_sha256") != input_basis_sha256
        ):
            raise ValueError("blind-run frozen row identity drifted")
        self._load_frozen_trace(question_id)
        if row.get("contract") == LARGE_RESULT_RAW_ROW_CONTRACT:
            self._validate_frozen_accepted_result(row, question_id=question_id)
        row["raw_row_sha256"] = stored
        return row

    def _validate_frozen_accepted_result(
        self, row: Mapping[str, object], *, question_id: str
    ) -> None:
        answer_view = row.get("answer_view")
        snapshot = row.get("gradeable_answer_snapshot")
        result_ref = (
            answer_view.get("accepted_result_ref")
            if isinstance(answer_view, Mapping)
            else None
        )
        expected_path = f"runs/{question_id}/accepted-result.json"
        if (
            not isinstance(answer_view, Mapping)
            or answer_view.get("contract")
            != "real_question_product_result_reference_view/v1"
            or answer_view.get("source_contract")
            != "lca_research_kernel.research_result_view/v1"
            or "result" in answer_view
            or not isinstance(snapshot, Mapping)
            or snapshot.get("contract")
            != "real_question_gradeable_answer_snapshot/v2"
            or not isinstance(result_ref, Mapping)
            or set(result_ref)
            != {"path", "sha256", "bytes", "accepted_result_sha256"}
            or result_ref.get("path") != expected_path
            or snapshot.get("accepted_result_ref") != result_ref
        ):
            raise ValueError("blind-run accepted result reference drifted")
        result_path = self.output_root / PurePosixPath(expected_path)
        if not result_path.exists():
            raise ValueError("blind-run accepted result is absent")
        _reject_root_symlink(result_path, "blind-run accepted result")
        content = _read_regular_no_follow(result_path, "blind-run accepted result")
        if (
            hashlib.sha256(content).hexdigest() != result_ref.get("sha256")
            or len(content) != result_ref.get("bytes")
        ):
            raise ValueError("blind-run accepted result bytes drifted")
        try:
            parsed = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("blind-run accepted result is unreadable") from error
        if not isinstance(parsed, Mapping):
            raise ValueError("blind-run accepted result must be an object")
        result = deepcopy(dict(parsed))
        result_sha256 = _canonical_sha256(result)
        reconstructed = {
            key: deepcopy(value)
            for key, value in answer_view.items()
            if key
            not in {
                "contract",
                "source_contract",
                "answer_view_sha256",
                "accepted_result_ref",
            }
        }
        reconstructed.update(
            {
                "contract": answer_view["source_contract"],
                "result": result,
            }
        )
        snapshot_unsigned = deepcopy(dict(snapshot))
        snapshot_sha256 = snapshot_unsigned.pop("snapshot_sha256", None)
        if (
            result_ref.get("accepted_result_sha256") != result_sha256
            or snapshot.get("accepted_result_sha256") != result_sha256
            or row.get("answer_sha256") != answer_view.get("answer_view_sha256")
            or answer_view.get("answer_view_sha256")
            != _canonical_sha256(reconstructed)
            or snapshot.get("answer_view_sha256") != row.get("answer_sha256")
            or snapshot_sha256 != _canonical_sha256(snapshot_unsigned)
        ):
            raise ValueError("blind-run accepted result identity drifted")

    def _load_frozen_trace(self, question_id: str) -> dict:
        path = self._trace_path(question_id)
        if not path.is_file() or path.is_symlink():
            raise ValueError("blind-run frozen trace is absent or linked")
        trace = _load_json(path, "blind-run frozen trace")
        stored = trace.pop("trace_snapshot_sha256", None)
        if (
            set(trace) != _TRACE_FIELDS - {"trace_snapshot_sha256"}
            or stored != _canonical_sha256(trace)
            or trace.get("question_id") != question_id
        ):
            raise ValueError("blind-run frozen trace identity drifted")
        trace["trace_snapshot_sha256"] = stored
        return trace

    def _atomic_run_package(
        self,
        question_id: str,
        row: Mapping[str, object],
        trace: Mapping[str, object],
        materialized: Mapping[str, bytes],
    ) -> None:
        runs_root = self.output_root / "runs"
        target = runs_root / question_id
        if target.exists():
            raise ValueError("blind-run output appeared concurrently")
        staged = Path(
            tempfile.mkdtemp(prefix=f".{question_id}-", dir=runs_root)
        )
        try:
            _atomic_json(staged / "answer.json", row)
            _atomic_json(staged / "trace.json", trace)
            for relative, content in sorted(materialized.items()):
                destination = staged / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            os.replace(staged, target)
        finally:
            shutil.rmtree(staged, ignore_errors=True)

    def _run_one(
        self,
        question: Mapping[str, object],
        verification: Mapping[str, object],
    ) -> dict:
        question_id = str(question["question_id"])
        input_basis = self._input_basis(question)
        input_basis_sha256 = _canonical_sha256(input_basis)
        path = self._row_path(question_id)
        frozen = self._load_frozen_row(
            path,
            question_id=question_id,
            input_basis_sha256=input_basis_sha256,
        )
        if frozen is not None:
            return frozen

        research_id = (
            f"blind-{_canonical_sha256(self.manifest)[:20]}-{question_id.lower()}"
        )
        question_content = {
            "contract": "real_question_material/v1",
            "question_id": question_id,
            "question_body": str(question["question_body"]).strip(),
            "image_refs": deepcopy(question["image_refs"]),
        }
        source_refs = [
            {
                "path": (
                    self.question_root / "real-question-pack.json"
                ).relative_to(self.execution_environment.project_root).as_posix()
                + f"#/questions/{question_id}",
                "sha256": _canonical_sha256(question),
                "authority": "user_supplied_project_input",
                "object_type": "real_question",
                "official_object_ids": [],
                "content": question_content,
            }
        ]
        for image_ref in question["image_refs"]:
            source_refs.append(
                {
                    "path": (self.question_root / image_ref["path"])
                    .relative_to(self.execution_environment.project_root)
                    .as_posix(),
                    "sha256": image_ref["sha256"],
                }
            )
        recorded_at = self.clock()
        request = {
            "contract": "lca_research_kernel.research_start/v1",
            "research_id": research_id,
            "title": f"Controlled real question {question_id}",
            "objective": question["question_body"].strip(),
            "source_refs": source_refs,
            "received_at": recorded_at,
        }
        start_status = None
        status_view = None
        answer_view = None
        calls = 0
        failure = None
        materialized: dict[str, bytes] = {}
        observations: list[Mapping[str, object] | None] = []
        stage = "start_research"
        try:
            calls += 1
            start_status = deepcopy(
                self.product_interface.start_research(deepcopy(request))
            )
            observations.append(start_status)
            stage = "read_status"
            calls += 1
            status_view = deepcopy(
                self.product_interface.read_status(research_id)
            )
            observations.append(status_view)
            stage = "read_result"
            calls += 1
            answer_view = deepcopy(
                self.product_interface.read_result(research_id)
            )
            observations.append(answer_view)
            resume = getattr(self.product_interface, "resume_research", None)
            resumes = 0
            while (
                _gradeable_answer_snapshot(
                    answer_view,
                    research_id=research_id,
                )
                is None
                and isinstance(status_view, Mapping)
                and status_view.get("status") == "active"
                and status_view.get("boundary") in _AUTOMATIC_RESUME_BOUNDARIES
                and callable(resume)
                and resumes < _MAX_AUTOMATIC_RESUMES
            ):
                stage = "resume_research"
                calls += 1
                observations.append(deepcopy(resume(research_id)))
                resumes += 1
                stage = "read_status"
                calls += 1
                status_view = deepcopy(
                    self.product_interface.read_status(research_id)
                )
                observations.append(status_view)
                stage = "read_result"
                calls += 1
                answer_view = deepcopy(
                    self.product_interface.read_result(research_id)
                )
                observations.append(answer_view)
        except Exception as error:
            observations.append(None)
            failure = {
                "failure_type": type(error).__name__,
                "failure_code": "product_interface_execution_failed",
                "failed_action": stage,
                "retryable": False,
            }
        gradeable_answer = _gradeable_answer_snapshot(
            answer_view,
            research_id=research_id,
        )
        if failure is None and gradeable_answer is None:
            failure = {
                "failure_type": "UnacceptedProductResult",
                "failure_code": "formal_result_not_accepted",
                "failed_action": "formal_result_validation",
                "observed_boundary": (
                    status_view.get("boundary")
                    if isinstance(status_view, Mapping)
                    else None
                ),
                "retryable": False,
            }
        status = "completed" if failure is None else "failed"
        payloads = tuple(observations) or (start_status, status_view, answer_view)
        effect_counts, effect_evidence, observed_effect_receipts = (
            _effect_evidence_snapshot(
                payloads,
                research_id=research_id,
            )
        )
        returned_trace_refs = _deduplicated_refs(
            payloads, "run_trace_ref"
        )
        trace_refs = (
            deepcopy(effect_evidence["registered_run_trace_refs"])
            if effect_evidence.get("status") == "verified"
            else returned_trace_refs
        )
        self._validate_effect_receipt_prefix_chain(
            observed_effect_receipts
        )
        self._materialize_run_trace_prefixes(
            effect_evidence["registered_run_trace_refs"],
            effect_counts=effect_counts,
        )
        official_refs, materialized = self._materialize_official_evidence(
            answer_view,
            question_id=question_id,
            research_id=research_id,
        )
        row = {
            "contract": RAW_ROW_CONTRACT,
            "question_id": question_id,
            "disposition": QUESTION_DISPOSITIONS[question_id],
            "research_id": research_id,
            "input_basis_sha256": input_basis_sha256,
            "request_sha256": _canonical_sha256(request),
            "blind_manifest_sha256": _canonical_sha256(self.manifest),
            "question_tree_sha256": self.manifest["question_tree_sha256"],
            "execution_identity_sha256": self.manifest[
                "execution_identity_sha256"
            ],
            "status": status,
            "start_status": start_status,
            "status_view": status_view,
            "answer_view": answer_view,
            "answer_sha256": (
                _canonical_sha256(answer_view)
                if isinstance(answer_view, Mapping)
                else None
            ),
            "gradeable_answer_snapshot": gradeable_answer,
            "timing_receipts": _deduplicated_timings(payloads),
            "trace_refs": trace_refs,
            "project_record_refs": _deduplicated_refs(
                payloads, "project_record_ref"
            ),
            "official_source_refs": official_refs,
            "product_interface_calls": calls,
            "failure": failure,
            "recorded_at": recorded_at,
        }
        try:
            self.execution_environment.validate_result(row)
        except ValueError as error:
            if str(error) != "project runtime output exceeds the size boundary":
                raise
            row, accepted_result_material = _content_address_large_accepted_result(
                row,
                question_id=question_id,
            )
            materialized.update(accepted_result_material)
            self.execution_environment.validate_result(row)
        row["raw_row_sha256"] = _canonical_sha256(row)
        trace = {
            "contract": "real_question_blind_trace_snapshot/v2",
            "question_id": question_id,
            "research_id": research_id,
            "request_sha256": row["request_sha256"],
            "status": status,
            "start_status": start_status,
            "status_view": status_view,
            "timing_receipts": deepcopy(row["timing_receipts"]),
            "trace_refs": deepcopy(row["trace_refs"]),
            "project_record_refs": deepcopy(row["project_record_refs"]),
            "effect_counts": effect_counts,
            "effect_evidence": effect_evidence,
            "events": [
                {
                    "contract": "real_question_blind_trace_event/v1",
                    "event_type": "question_input_bound",
                    "source_ref": {
                        "path": source_refs[0]["path"],
                        "sha256": source_refs[0]["sha256"],
                    },
                }
            ],
            "product_interface_calls": calls,
            "failure": deepcopy(failure),
            "recorded_at": recorded_at,
        }
        self.execution_environment.validate_result(trace)
        trace["trace_snapshot_sha256"] = _canonical_sha256(trace)
        self._atomic_run_package(question_id, row, trace, materialized)
        return deepcopy(row)

    def _materialize_run_trace_prefixes(
        self,
        refs: object,
        *,
        effect_counts: Mapping[str, object],
    ) -> None:
        if refs == []:
            return
        product_root = getattr(self.product_interface, "root", None)
        if not isinstance(product_root, (str, Path)):
            raise ValueError("Product Interface Run Trace root is absent")
        source_root = Path(product_root).resolve()
        if not isinstance(refs, list) or not refs:
            raise ValueError("registered Run Trace references are absent")
        observed_external_writes = 0
        for value in refs:
            ref = _validated_run_trace_ref(value)
            prefix, events = self._verified_run_trace_prefix(
                source_root,
                ref,
            )
            observed_external_writes += sum(
                event.get("event_type")
                == "research.run.capability.completed"
                and isinstance(event.get("payload"), Mapping)
                and event["payload"].get("effect") == "external_write"
                for event in events
            )
            destination = self.output_root / Path(ref["path"])
            self.execution_environment.authorize_file(destination, "write")
            if destination.exists() and destination.read_bytes() != prefix:
                raise ValueError("materialized Run Trace prefix drifted")
            _atomic_bytes(destination, prefix)
        if effect_counts.get("external_write") != observed_external_writes:
            raise ValueError("Run Trace effect count drifted")

    def _validate_effect_receipt_prefix_chain(
        self,
        receipts: Sequence[Mapping[str, object]],
    ) -> None:
        if not receipts:
            return
        product_root = getattr(self.product_interface, "root", None)
        if not isinstance(product_root, (str, Path)):
            raise ValueError("Product Interface Run Trace root is absent")
        source_root = Path(product_root).resolve()
        for receipt in receipts:
            observed_external_writes = 0
            for value in receipt["registered_run_trace_refs"]:
                ref = _validated_run_trace_ref(value)
                _, events = self._verified_run_trace_prefix(
                    source_root,
                    ref,
                )
                observed_external_writes += sum(
                    event.get("event_type")
                    == "research.run.capability.completed"
                    and isinstance(event.get("payload"), Mapping)
                    and event["payload"].get("effect") == "external_write"
                    for event in events
                )
            if (
                receipt["effect_counts"]["external_write"]
                != observed_external_writes
            ):
                raise ValueError("effect receipt Run Trace effect count drifted")

    @staticmethod
    def _verified_run_trace_prefix(
        source_root: Path,
        ref: Mapping[str, object],
    ) -> tuple[bytes, list[Mapping[str, object]]]:
        normalized = _validated_run_trace_ref(ref)
        relative = Path(normalized["path"])
        source = source_root / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError("registered Run Trace member is absent or linked")
        resolved = source.resolve()
        if source_root != resolved and source_root not in resolved.parents:
            raise ValueError("registered Run Trace member escaped its root")
        lines = source.read_bytes().splitlines(keepends=True)
        if (
            len(lines) < normalized["event_count"]
            or any(not line.endswith(b"\n") for line in lines)
        ):
            raise ValueError("registered Run Trace prefix is incomplete")
        prefix_lines = lines[: normalized["event_count"]]
        prefix = b"".join(prefix_lines)
        events: list[Mapping[str, object]] = []
        previous_event_sha256 = None
        try:
            for sequence, line in enumerate(prefix_lines, start=1):
                event = json.loads(line)
                if not isinstance(event, Mapping):
                    raise ValueError
                unsigned = deepcopy(dict(event))
                event_sha256 = unsigned.pop("event_sha256", None)
                if (
                    event.get("contract")
                    != "lca_research_kernel.run_trace_event/v1"
                    or event.get("sequence") != sequence
                    or event.get("run_id") != normalized["run_id"]
                    or event.get("previous_event_sha256")
                    != previous_event_sha256
                    or event_sha256 != _canonical_sha256(unsigned)
                ):
                    raise ValueError
                previous_event_sha256 = event_sha256
                events.append(event)
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(
                "registered Run Trace prefix is invalid"
            ) from error
        if (
            hashlib.sha256(prefix).hexdigest() != normalized["trace_sha256"]
            or events[-1].get("event_sha256")
            != normalized["last_event_sha256"]
        ):
            raise ValueError("registered Run Trace prefix identity drifted")
        return prefix, events

    def _materialize_official_evidence(
        self,
        answer_view: object,
        *,
        question_id: str,
        research_id: str,
    ) -> tuple[list[dict], dict[str, bytes]]:
        if not isinstance(answer_view, Mapping):
            return [], {}
        result = answer_view.get("result")
        if not isinstance(result, Mapping):
            return [], {}
        evidence = result.get("official_source_evidence")
        if evidence is None:
            legacy = _official_source_refs(answer_view)
            if legacy:
                raise ValueError(
                    "official evidence must be materialized from accepted cache references"
                )
            return [], {}
        verified_trace_ref = _validated_run_trace_ref(
            answer_view.get("run_trace_ref")
        )
        effect_receipt = answer_view.get("effect_receipt")
        registered_trace_refs = (
            effect_receipt.get("registered_run_trace_refs")
            if isinstance(effect_receipt, Mapping)
            else None
        )
        if not isinstance(registered_trace_refs, list):
            raise ValueError("accepted official evidence Run Trace set is absent")
        normalized_trace_refs = [
            _validated_run_trace_ref(ref) for ref in registered_trace_refs
        ]
        registered_trace_identities = {
            _canonical_sha256(ref) for ref in normalized_trace_refs
        }
        if (
            len(normalized_trace_refs) != len(registered_trace_identities)
            or _canonical_sha256(verified_trace_ref)
            not in registered_trace_identities
        ):
            raise ValueError("accepted official evidence Run Trace set drifted")
        registered_run_ids = {
            ref["run_id"] for ref in normalized_trace_refs
        }
        if (
            self.official_source_cache_root is None
            or not isinstance(evidence, list)
            or any(not isinstance(item, Mapping) for item in evidence)
        ):
            raise ValueError("accepted official source evidence is invalid")
        refs: list[dict] = []
        files: dict[str, bytes] = {}
        for index, item in enumerate(evidence):
            if set(item) != {"source_receipt", "cache_ref", "fact_excerpt"}:
                raise ValueError("accepted official source evidence is incomplete")
            receipt = deepcopy(dict(item["source_receipt"]))
            excerpt = deepcopy(dict(item["fact_excerpt"]))
            cache_ref = item["cache_ref"]
            if (
                receipt.get("contract") != "official_public_source_receipt/v1"
                or receipt.get("question_id") != research_id
                or receipt.get("run_id") not in registered_run_ids
                or not isinstance(cache_ref, Mapping)
                or cache_ref.get("contract")
                != "official_public_source_cache_ref/v1"
                or excerpt.get("contract") != "official_source_fact_excerpt/v1"
                or excerpt.get("question_id") != research_id
            ):
                raise ValueError("accepted official evidence question or run drifted")
            receipt_unsigned = deepcopy(receipt)
            receipt_identity = receipt_unsigned.pop("receipt_sha256", None)
            excerpt_unsigned = deepcopy(excerpt)
            excerpt_identity = excerpt_unsigned.pop("excerpt_sha256", None)
            if (
                receipt_identity != _canonical_sha256(receipt_unsigned)
                or excerpt_identity != _canonical_sha256(excerpt_unsigned)
                or excerpt.get("receipt_sha256") != receipt_identity
            ):
                raise ValueError("accepted official evidence identity drifted")
            relative = Path(str(cache_ref.get("path", "")))
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or "\\" in str(cache_ref.get("path", ""))
            ):
                raise ValueError("official source cache reference is invalid")
            source = self.official_source_cache_root / relative
            if source.is_symlink() or not source.is_file():
                raise ValueError("official source cache member is absent or linked")
            resolved = source.resolve()
            if self.official_source_cache_root not in resolved.parents:
                raise ValueError("official source cache member escaped its root")
            body = source.read_bytes()
            body_sha = hashlib.sha256(body).hexdigest()
            if (
                cache_ref.get("sha256") != body_sha
                or cache_ref.get("bytes") != len(body)
                or receipt.get("body_sha256") != body_sha
                or receipt.get("body_bytes") != len(body)
                or excerpt.get("source_body_sha256") != body_sha
            ):
                raise ValueError("official source cache member identity drifted")
            receipt_bytes = _canonical_bytes(receipt) + b"\n"
            excerpt_bytes = _canonical_bytes(excerpt) + b"\n"
            base = f"official/{index:02d}"
            paths = {
                "raw_path": f"runs/{question_id}/{base}-body.bin",
                "receipt_path": f"runs/{question_id}/{base}-receipt.json",
                "excerpt_path": f"runs/{question_id}/{base}-excerpt.json",
            }
            for output_path in paths.values():
                self.execution_environment.authorize_file(
                    self.output_root / output_path,
                    "write",
                )
            files[f"{base}-body.bin"] = body
            files[f"{base}-receipt.json"] = receipt_bytes
            files[f"{base}-excerpt.json"] = excerpt_bytes
            refs.append(
                {
                    "raw_path": paths["raw_path"],
                    "raw_sha256": body_sha,
                    "receipt_path": paths["receipt_path"],
                    "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
                    "excerpt_path": paths["excerpt_path"],
                    "excerpt_sha256": hashlib.sha256(excerpt_bytes).hexdigest(),
                }
            )
        return refs, files

    def _write_indexes(self, selected: list[str]) -> dict:
        rows: dict[str, dict] = {}
        for question_id in SCORED_QUESTION_IDS:
            path = self._row_path(question_id)
            if not path.is_file():
                continue
            question = self._questions[question_id]
            loaded = self._load_frozen_row(
                path,
                question_id=question_id,
                input_basis_sha256=_canonical_sha256(
                    self._input_basis(question)
                ),
            )
            if loaded is None:
                raise ValueError("blind-run row disappeared during indexing")
            rows[question_id] = loaded
        completed = [
            question_id for question_id in SCORED_QUESTION_IDS if question_id in rows
        ]
        failed = [
            question_id
            for question_id in completed
            if rows[question_id]["status"] == "failed"
        ]
        progress = {
            "contract": PROGRESS_INDEX_CONTRACT,
            "question_ids": deepcopy(SCORED_QUESTION_IDS),
            "selected_question_ids": deepcopy(selected),
            "completed_question_ids": completed,
            "pending_question_ids": [
                question_id
                for question_id in SCORED_QUESTION_IDS
                if question_id not in rows
            ],
            "failed_question_ids": failed,
            "failure_count": len(failed),
            "row_refs": [
                {
                    "question_id": question_id,
                    "path": f"runs/{question_id}/answer.json",
                    "sha256": _file_sha256(self._row_path(question_id)),
                }
                for question_id in completed
            ],
            "blind_manifest_sha256": _canonical_sha256(self.manifest),
        }
        progress["progress_sha256"] = _canonical_sha256(progress)
        self.execution_environment.validate_result(progress)
        _atomic_json(
            self.output_root / "blind-run-progress-index.json",
            progress,
        )
        if completed == SCORED_QUESTION_IDS:
            runs = []
            for question_id in SCORED_QUESTION_IDS:
                answer_path = self._row_path(question_id)
                trace_path = self._trace_path(question_id)
                row = rows[question_id]
                runs.append(
                    {
                        "question_id": question_id,
                        "research_id": row["research_id"],
                        "answer_path": answer_path.relative_to(
                            self.output_root
                        ).as_posix(),
                        "answer_sha256": _file_sha256(answer_path),
                        "trace_path": trace_path.relative_to(
                            self.output_root
                        ).as_posix(),
                        "trace_sha256": _file_sha256(trace_path),
                        "official_source_refs": deepcopy(
                            row["official_source_refs"]
                        ),
                    }
                )
            final = {
                "contract": FINAL_INDEX_CONTRACT,
                "runs": runs,
                "failure_count": len(failed),
            }
            _atomic_json(self.output_root / "run-index.json", final)
        return deepcopy(progress)


__all__ = ["RealQuestionBlindRunner"]
