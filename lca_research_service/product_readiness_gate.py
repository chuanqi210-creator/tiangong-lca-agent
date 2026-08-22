"""Black-box Product Readiness Gate evaluators for one exact runtime release."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .product_evidence import (
    validate_service_idempotency_replay_evidence,
    validate_service_product_evidence,
)


DIRECT_JOURNEY = "direct_no_material_explanation"
DIRECT_MAX_FIRST_MODEL_MS = 5_000
DIRECT_MAX_FINAL_MS = 30_000
MATERIAL_JOURNEY = "medium_material_explanation"
MATERIAL_PROFILE_ID = "tidas-lifecyclemodels-01.01.011-real-package"
MATERIAL_SOURCE_SHA256 = (
    "f04941a4bdfaa78dbeff9b629df164e5bba97025d56ffaef5cc572b6cf7f8e58"
)
MATERIAL_SOURCE_BYTES = 2_050_097
MATERIAL_QUALIFIED_LOCATOR_COUNT = 595
MATERIAL_MAX_FIRST_MODEL_MS = 20_000
MATERIAL_MAX_FINAL_MS = 90_000
MATERIAL_MAX_CPU_MS = 300_000
MATERIAL_MAX_CHECKPOINT_EVENTS = 512
OFFICIAL_JOURNEY = "official_source_lca_question"
OFFICIAL_MAX_FIRST_MODEL_MS = 30_000
OFFICIAL_MAX_FINAL_MS = 480_000
MISSING_DATA_JOURNEY = "professional_missing_data_recovery"
MISSING_DATA_MAX_WAITING_MS = 30_000
MISSING_DATA_MAX_FINAL_MS = 90_000
MISSING_DATA_MAX_EVENT_GROWTH = 16
NATIVE_JOURNEY = "tiangong_native_bounded_contract"
NATIVE_MAX_FIRST_MODEL_MS = 30_000
NATIVE_MAX_FINAL_MS = 90_000
MISSING_DATA_FIELD_LABELS = {
    "allocation_rule": "分配规则",
    "functional_unit": "功能单位",
    "geographical_scope": "地理范围",
    "impact_assessment_method": "影响评价方法",
    "inventory_data": "清单数据",
    "reference_flow": "参考流",
    "scenario_parameters": "情景参数",
    "system_boundary": "系统边界",
    "technology_scope": "技术范围",
    "temporal_scope": "时间范围",
}
PRODUCT_READINESS_JOURNEYS = {
    DIRECT_JOURNEY,
    MATERIAL_JOURNEY,
    OFFICIAL_JOURNEY,
    MISSING_DATA_JOURNEY,
    NATIVE_JOURNEY,
}
PRODUCT_RELEASE_ROLES = {
    "backend_service",
    "accepted_job_request",
    "worker_execution",
    "result_projection",
}


def browser_capture_authentication_message(
    *, evidence_sha256: str, gate_run_id: str
) -> bytes:
    """Bind one controlled capture to both its evidence and exact gate run."""

    if re.fullmatch(r"[0-9a-f]{64}", evidence_sha256) is None:
        raise ValueError("browser capture evidence identity is invalid")
    if not gate_run_id:
        raise ValueError("browser gate run identity is absent")
    return (
        b"d18.product-readiness-browser-capture/v1\0"
        + gate_run_id.encode("utf-8")
        + b"\0"
        + evidence_sha256.encode("ascii")
    )


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _execution_receipt_errors(
    value: object,
    *,
    prefix: str = "direct",
    expected_counts: Mapping[str, int] | None = None,
    max_first_model_ms: int = DIRECT_MAX_FIRST_MODEL_MS,
    max_final_ms: int | None = DIRECT_MAX_FINAL_MS,
    require_run_control: bool = False,
) -> list[str]:
    if not isinstance(value, Mapping):
        return ["execution_receipt_absent"]
    unsigned = {key: deepcopy(item) for key, item in value.items() if key != "receipt_sha256"}
    errors = []
    if value.get("contract") != "lca_research_kernel.public_execution_receipt/v1":
        errors.append("execution_receipt_contract_invalid")
    if value.get("receipt_sha256") != canonical_sha256(unsigned):
        errors.append("execution_receipt_hash_invalid")
    counts = value.get("counts")
    if not isinstance(counts, Mapping):
        return [*errors, "execution_counts_absent"]
    expected = dict(
        expected_counts
        or {
            "model": 1,
            "tool": 0,
            "external_read": 0,
            "external_write": 0,
            "useless_retry": 0,
        }
    )
    if set(counts) != set(expected):
        errors.append(f"{prefix}_execution_counts_invalid")
    for key, expected_value in expected.items():
        if counts.get(key) != expected_value:
            errors.append(f"{prefix}_{key}_count_invalid")
    model_calls = value.get("model_calls")
    expected_model_calls = expected.get("model", 1)
    if (
        not isinstance(model_calls, list)
        or len(model_calls) != expected_model_calls
    ):
        errors.append(f"{prefix}_model_receipt_invalid")
    elif any(
        not isinstance(call, Mapping)
        or any(
            not isinstance(call.get(key), str) or not call[key]
            for key in ("provider", "model", "adapter_id")
        )
        for call in model_calls
    ):
        errors.append(f"{prefix}_model_identity_invalid")
    first_model = value.get("first_model_request_ms")
    if (
        isinstance(first_model, bool)
        or not isinstance(first_model, int)
        or first_model < 0
        or first_model > max_first_model_ms
    ):
        errors.append(f"{prefix}_first_model_budget_exceeded")
    elapsed = value.get("elapsed_ms")
    formal = elapsed.get("request_to_formal_acceptance") if isinstance(elapsed, Mapping) else None
    if max_final_ms is not None and (
        isinstance(formal, bool)
        or not isinstance(formal, int)
        or formal < 0
        or formal > max_final_ms
    ):
        errors.append(f"{prefix}_formal_result_budget_exceeded")
    trace_refs = value.get("trace_refs")
    if not isinstance(trace_refs, list) or not trace_refs:
        errors.append("execution_trace_receipt_absent")
    elif any("path" in reference for reference in trace_refs if isinstance(reference, Mapping)):
        errors.append("execution_trace_path_leaked")
    project_record = value.get("project_record")
    if (
        not isinstance(project_record, Mapping)
        or not isinstance(project_record.get("last_event_sha256"), str)
        or len(project_record["last_event_sha256"]) != 64
        or isinstance(project_record.get("event_count"), bool)
        or not isinstance(project_record.get("event_count"), int)
        or project_record["event_count"] < 1
    ):
        errors.append("project_record_receipt_invalid")
    if require_run_control:
        control = value.get("run_control")
        if not isinstance(control, Mapping):
            errors.append(f"{prefix}_run_control_receipt_absent")
        else:
            heartbeat_count = control.get("heartbeat_count")
            cpu_sample_count = control.get("cpu_sample_count")
            cpu_ms = control.get("max_observed_cpu_ms")
            elapsed_ms = control.get("max_observed_elapsed_ms")
            checkpoint_events = control.get("max_checkpoint_event_count")
            if (
                isinstance(heartbeat_count, bool)
                or not isinstance(heartbeat_count, int)
                or heartbeat_count < 1
                or isinstance(cpu_sample_count, bool)
                or not isinstance(cpu_sample_count, int)
                or cpu_sample_count < 1
            ):
                errors.append(f"{prefix}_run_control_sample_absent")
            if (
                isinstance(cpu_ms, bool)
                or not isinstance(cpu_ms, int)
                or cpu_ms < 0
                or cpu_ms > MATERIAL_MAX_CPU_MS
            ):
                errors.append(f"{prefix}_cpu_budget_exceeded")
            if (
                isinstance(elapsed_ms, bool)
                or not isinstance(elapsed_ms, int)
                or elapsed_ms < 0
                or elapsed_ms > max_final_ms
            ):
                errors.append(f"{prefix}_checkpoint_elapsed_budget_exceeded")
            if (
                isinstance(checkpoint_events, bool)
                or not isinstance(checkpoint_events, int)
                or checkpoint_events < 0
                or checkpoint_events > MATERIAL_MAX_CHECKPOINT_EVENTS
            ):
                errors.append(f"{prefix}_event_budget_exceeded")
    return errors


def _rendered_answer_fragments(value: object) -> list[str]:
    """Return the text fragments a browser must expose after basic Markdown layout."""

    if not isinstance(value, str):
        return []
    fragments: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^(?:#{1,6}\s+|[-*+]\s+|>\s+|\d+[.)]\s+)", "", line)
        line = line.strip()
        if line:
            fragments.append(line)
    return fragments


def evaluate_direct_journey(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate journey A without reading service internals or hidden trace bytes."""

    errors: list[str] = []
    if observation.get("contract") != "d18.product_readiness_observation/v1":
        errors.append("observation_contract_invalid")
    if observation.get("journey") != DIRECT_JOURNEY:
        errors.append("journey_identity_invalid")
    releases = observation.get("release_identities")
    if not isinstance(releases, list) or len(releases) < 4:
        errors.append("release_identity_evidence_incomplete")
        release_id = None
    else:
        release_ids = {
            json.dumps(value, sort_keys=True, separators=(",", ":"))
            for value in releases
            if isinstance(value, Mapping)
        }
        release_id = releases[0].get("release_id") if isinstance(releases[0], Mapping) else None
        if len(release_ids) != 1 or not isinstance(release_id, str):
            errors.append("release_identity_mismatch")
    session_release = observation.get("session_release")
    if (
        not isinstance(session_release, Mapping)
        or session_release.get("release_id") != release_id
        or (
            isinstance(releases, list)
            and releases
            and isinstance(releases[0], Mapping)
            and session_release.get("frontend_build_id")
            != releases[0].get("frontend_build_id")
        )
    ):
        errors.append("frontend_session_release_mismatch")
    request = observation.get("request")
    if not isinstance(request, Mapping):
        errors.append("request_evidence_absent")
    elif request.get("material_ids") or request.get("source_materials"):
        errors.append("direct_path_received_materials")
    wall_elapsed = observation.get("wall_elapsed_ms")
    if (
        isinstance(wall_elapsed, bool)
        or not isinstance(wall_elapsed, int)
        or wall_elapsed < 0
        or wall_elapsed > DIRECT_MAX_FINAL_MS
    ):
        errors.append("direct_wall_clock_budget_exceeded")
    sequences = observation.get("sse_sequences")
    if (
        not isinstance(sequences, list)
        or not sequences
        or any(isinstance(item, bool) or not isinstance(item, int) for item in sequences)
        or sequences != sorted(set(sequences))
    ):
        errors.append("sse_sequence_invalid")
    result_view = observation.get("result_view")
    result = result_view.get("result") if isinstance(result_view, Mapping) else None
    if not isinstance(result_view, Mapping) or result_view.get("status") != "accepted":
        errors.append("direct_result_not_accepted")
    if not isinstance(result, Mapping):
        errors.append("direct_result_projection_absent")
    else:
        answer = result.get("bounded_conclusion") or result.get("summary")
        if not isinstance(answer, str) or not answer.strip():
            errors.append("direct_answer_absent")
        if result.get("routeFamily") != "direct_fast_path":
            errors.append("direct_route_family_invalid")
        if result.get("claim_ceiling") != "candidate_only":
            errors.append("direct_claim_ceiling_invalid")
        for field in ("values", "official_object_ids", "source_sha256s", "citations"):
            if result.get(field) not in ([], None):
                errors.append(f"direct_{field}_must_be_empty")
        limitations = result.get("limitations")
        if not isinstance(limitations, list) or not limitations:
            errors.append("direct_limitation_absent")
        browser = observation.get("browser_view")
        request_research_id = (
            request.get("research_id") if isinstance(request, Mapping) else None
        )
        browser_text = (
            browser.get("visible_text") if isinstance(browser, Mapping) else None
        )
        page_url = browser.get("page_url") if isinstance(browser, Mapping) else None
        page_path = urlsplit(page_url).path if isinstance(page_url, str) else None
        expected_text = [answer, *(limitations if isinstance(limitations, list) else [])]
        if (
            not isinstance(browser, Mapping)
            or browser.get("contract") != "d18.browser_product_observation/v1"
            or browser.get("release_id") != release_id
            or browser.get("research_id") != request_research_id
            or browser.get("job_id") != observation.get("job_id")
            or page_path != f"/research/{request_research_id}"
            or not isinstance(browser_text, str)
            or any(
                not isinstance(value, str) or not value or value not in browser_text
                for value in expected_text
            )
        ):
            errors.append("direct_browser_result_invalid")
    execution = (
        result_view.get("executionReceipt")
        if isinstance(result_view, Mapping)
        else None
    )
    errors.extend(_execution_receipt_errors(execution))
    retry = observation.get("retry")
    if not isinstance(retry, Mapping):
        errors.append("direct_retry_evidence_absent")
    else:
        if retry.get("same_job_id") is not True:
            errors.append("direct_retry_job_identity_drifted")
        if retry.get("result_sha256_before") != retry.get("result_sha256_after"):
            errors.append("direct_retry_result_drifted")
        for label in (
            "execution_receipt_sha256",
            "project_record_sha256",
            "project_record_event_count",
            "sse_sha256",
        ):
            if retry.get(f"{label}_before") != retry.get(f"{label}_after"):
                errors.append(f"direct_retry_{label}_drifted")
    receipt = {
        "contract": "d18.product_readiness_journey_receipt/v1",
        "journey": DIRECT_JOURNEY,
        "status": "passed" if not errors else "failed",
        "release_id": release_id,
        "errors": errors,
        "observation_sha256": canonical_sha256(observation),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def _release_identity(
    observation: Mapping[str, Any], errors: list[str]
) -> str | None:
    releases = observation.get("release_identities")
    if not isinstance(releases, list) or len(releases) < 4:
        errors.append("release_identity_evidence_incomplete")
        return None
    release_ids = {
        json.dumps(value, sort_keys=True, separators=(",", ":"))
        for value in releases
        if isinstance(value, Mapping)
    }
    release_id = releases[0].get("release_id") if isinstance(releases[0], Mapping) else None
    if len(release_ids) != 1 or not isinstance(release_id, str):
        errors.append("release_identity_mismatch")
    session_release = observation.get("session_release")
    if (
        not isinstance(session_release, Mapping)
        or session_release.get("release_id") != release_id
        or not isinstance(releases[0], Mapping)
        or session_release.get("frontend_build_id")
        != releases[0].get("frontend_build_id")
    ):
        errors.append("frontend_session_release_mismatch")
    return release_id if isinstance(release_id, str) else None


def _representative_limitation_present(result: Mapping[str, Any]) -> bool:
    values = list(result.get("limitations") or [])
    for limitation in result.get("materialLimitations") or []:
        if isinstance(limitation, Mapping):
            values.append(limitation.get("message"))
    normalized = " ".join(item.casefold() for item in values if isinstance(item, str))
    return (
        ("代表" in normalized or "representative" in normalized)
        and (
            "非穷尽" in normalized
            or ("穷尽" in normalized and ("并非" in normalized or "不是" in normalized))
            or "not exhaustive" in normalized
            or "non-exhaustive" in normalized
            or "not a complete" in normalized
        )
    )


def evaluate_material_journey(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate the content-bound real TIDAS package product journey."""

    errors: list[str] = []
    if observation.get("contract") != "d18.product_readiness_observation/v1":
        errors.append("observation_contract_invalid")
    if observation.get("journey") != MATERIAL_JOURNEY:
        errors.append("journey_identity_invalid")
    release_id = _release_identity(observation, errors)
    source = observation.get("source")
    source_filename = source.get("filename") if isinstance(source, Mapping) else None
    if (
        not isinstance(source, Mapping)
        or source.get("profile_id") != MATERIAL_PROFILE_ID
        or source.get("sha256") != MATERIAL_SOURCE_SHA256
        or source.get("bytes") != MATERIAL_SOURCE_BYTES
        or not isinstance(source_filename, str)
        or not source_filename.strip()
    ):
        errors.append("material_source_fixture_invalid")
    request = observation.get("request")
    material = observation.get("material")
    material_id = material.get("material_id") if isinstance(material, Mapping) else None
    revision = material.get("current_revision") if isinstance(material, Mapping) else None
    revision_id = revision.get("revision_id") if isinstance(revision, Mapping) else None
    if (
        not isinstance(request, Mapping)
        or request.get("material_ids") != [material_id]
        or not isinstance(material_id, str)
        or material.get("project_id") != request.get("research_id")
        or material.get("byte_size") != MATERIAL_SOURCE_BYTES
        or material.get("display_name") != source_filename
        or not isinstance(revision, Mapping)
        or revision.get("input_artifact_id") != MATERIAL_SOURCE_SHA256
        or revision.get("processing_status") != "ready"
        or revision.get("scientific_status") != "accepted_project_input"
        or not isinstance(revision_id, str)
    ):
        errors.append("material_public_identity_invalid")
    upload_ms = observation.get("upload_to_ready_ms")
    if (
        isinstance(upload_ms, bool)
        or not isinstance(upload_ms, int)
        or upload_ms < 0
        or upload_ms > MATERIAL_MAX_FINAL_MS
    ):
        errors.append("material_processing_budget_exceeded")
    wall_elapsed = observation.get("wall_elapsed_ms")
    if (
        isinstance(wall_elapsed, bool)
        or not isinstance(wall_elapsed, int)
        or wall_elapsed < 0
        or wall_elapsed > MATERIAL_MAX_FINAL_MS
    ):
        errors.append("material_wall_clock_budget_exceeded")
    sequences = observation.get("sse_sequences")
    if (
        not isinstance(sequences, list)
        or not sequences
        or any(isinstance(item, bool) or not isinstance(item, int) for item in sequences)
        or sequences != sorted(set(sequences))
    ):
        errors.append("sse_sequence_invalid")
    result_view = observation.get("result_view")
    result = result_view.get("result") if isinstance(result_view, Mapping) else None
    if not isinstance(result_view, Mapping) or result_view.get("status") != "accepted":
        errors.append("material_result_not_accepted")
    if not isinstance(result, Mapping):
        errors.append("material_result_projection_absent")
    else:
        answer = result.get("bounded_conclusion") or result.get("summary")
        if not isinstance(answer, str) or not answer.strip():
            errors.append("material_answer_absent")
        if result.get("routeFamily") != "evidence_research":
            errors.append("material_route_family_invalid")
        if result.get("claim_ceiling") != "source_bounded_interpretation":
            errors.append("material_claim_ceiling_invalid")
        citations = result.get("citations")
        if (
            not isinstance(citations, list)
            or not citations
            or any(
                not isinstance(citation, Mapping)
                or citation.get("materialId") != material_id
                or citation.get("revisionId") != revision_id
                or not isinstance(citation.get("locator"), Mapping)
                or not isinstance(citation.get("evidenceUrl"), str)
                or not citation["evidenceUrl"]
                for citation in citations
            )
        ):
            errors.append("material_citation_binding_invalid")
        source_use = result.get("sourceUse")
        if (
            not isinstance(source_use, list)
            or len(source_use) != 1
            or not isinstance(source_use[0], Mapping)
            or source_use[0].get("materialId") != material_id
            or source_use[0].get("revisionId") != revision_id
            or source_use[0].get("status") != "used"
            or not isinstance(source_use[0].get("selectedLocatorCount"), int)
            or source_use[0]["selectedLocatorCount"] < 1
            or not isinstance(source_use[0].get("citedLocatorCount"), int)
            or source_use[0]["citedLocatorCount"] < 1
        ):
            errors.append("material_source_use_invalid")
        if not _representative_limitation_present(result):
            errors.append("material_representative_limitation_absent")
        browser = observation.get("browser_view")
        request_research_id = (
            request.get("research_id") if isinstance(request, Mapping) else None
        )
        browser_text = (
            browser.get("visible_text") if isinstance(browser, Mapping) else None
        )
        page_url = browser.get("page_url") if isinstance(browser, Mapping) else None
        page_path = urlsplit(page_url).path if isinstance(page_url, str) else None
        expected_text = [
            answer,
            "精确引用",
            "本轮来源使用",
            "已采用",
            *(str(item.get("displayName")) for item in citations if isinstance(item, Mapping)),
            *(str(item) for item in result.get("limitations") or []),
            *(
                str(item.get("message"))
                for item in result.get("materialLimitations") or []
                if isinstance(item, Mapping)
            ),
        ]
        browser_upload_ms = (
            browser.get("upload_to_ready_ms") if isinstance(browser, Mapping) else None
        )
        if (
            not isinstance(browser, Mapping)
            or browser.get("contract") != "d18.browser_product_observation/v1"
            or browser.get("release_id") != release_id
            or browser.get("research_id") != request_research_id
            or browser.get("job_id") != observation.get("job_id")
            or page_path != f"/research/{request_research_id}"
            or browser_upload_ms != upload_ms
            or not isinstance(browser_text, str)
            or any(
                not isinstance(value, str) or not value or value not in browser_text
                for value in expected_text
            )
        ):
            errors.append("material_browser_result_invalid")
    execution = result_view.get("executionReceipt") if isinstance(result_view, Mapping) else None
    errors.extend(
        _execution_receipt_errors(
            execution,
            prefix="material",
            expected_counts={
                "model": 2,
                "tool": 1,
                "external_read": 0,
                "external_write": 0,
                "useless_retry": 0,
            },
            max_first_model_ms=MATERIAL_MAX_FIRST_MODEL_MS,
            max_final_ms=MATERIAL_MAX_FINAL_MS,
            require_run_control=True,
        )
    )
    retry = observation.get("retry")
    if not isinstance(retry, Mapping) or retry.get("same_job_id") is not True:
        errors.append("material_retry_job_identity_drifted")
    elif retry.get("result_sha256_before") != retry.get("result_sha256_after"):
        errors.append("material_retry_result_drifted")
    if isinstance(retry, Mapping):
        for label in (
            "execution_receipt_sha256",
            "project_record_sha256",
            "project_record_event_count",
            "sse_sha256",
        ):
            if retry.get(f"{label}_before") != retry.get(f"{label}_after"):
                errors.append(f"material_retry_{label}_drifted")
    receipt = {
        "contract": "d18.product_readiness_journey_receipt/v1",
        "journey": MATERIAL_JOURNEY,
        "status": "passed" if not errors else "failed",
        "release_id": release_id,
        "material_profile_id": MATERIAL_PROFILE_ID,
        "source_sha256": MATERIAL_SOURCE_SHA256,
        "qualified_source_locator_count": MATERIAL_QUALIFIED_LOCATOR_COUNT,
        "errors": errors,
        "observation_sha256": canonical_sha256(observation),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def evaluate_official_journey(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one bounded official-source LCA research journey."""

    errors: list[str] = []
    if observation.get("contract") != "d18.product_readiness_observation/v1":
        errors.append("observation_contract_invalid")
    if observation.get("journey") != OFFICIAL_JOURNEY:
        errors.append("journey_identity_invalid")
    release_id = _release_identity(observation, errors)
    request = observation.get("request")
    if (
        not isinstance(request, Mapping)
        or not isinstance(request.get("research_id"), str)
        or request.get("material_ids") not in (None, [])
        or request.get("source_materials") not in (None, [])
    ):
        errors.append("official_request_boundary_invalid")
    wall_elapsed = observation.get("wall_elapsed_ms")
    if (
        isinstance(wall_elapsed, bool)
        or not isinstance(wall_elapsed, int)
        or wall_elapsed < 0
        or wall_elapsed > OFFICIAL_MAX_FINAL_MS
    ):
        errors.append("official_wall_clock_budget_exceeded")
    sequences = observation.get("sse_sequences")
    if (
        not isinstance(sequences, list)
        or not sequences
        or any(isinstance(item, bool) or not isinstance(item, int) for item in sequences)
        or sequences != sorted(set(sequences))
    ):
        errors.append("sse_sequence_invalid")
    result_view = observation.get("result_view")
    result = result_view.get("result") if isinstance(result_view, Mapping) else None
    if not isinstance(result_view, Mapping) or result_view.get("status") != "accepted":
        errors.append("official_result_not_accepted")
    if not isinstance(result, Mapping):
        errors.append("official_result_projection_absent")
    else:
        result_status = result.get("status")
        answer = (
            result.get("answer")
            if result_status == "formal_result_accepted"
            else result.get("bounded_conclusion")
        )
        if not isinstance(answer, str) or not answer.strip():
            errors.append("official_answer_absent")
        if result.get("routeFamily") != "evidence_research":
            errors.append("official_route_family_invalid")
        claim_ceiling = result.get("claim_ceiling")
        if (result_status, claim_ceiling) not in {
            ("formal_result_accepted", "source_bounded_interpretation"),
            ("formal_limited_result_accepted", "calculation_structure_only"),
        }:
            errors.append("official_claim_ceiling_invalid")
        if result_status == "formal_limited_result_accepted":
            limitations = result.get("limitations")
            if (
                not isinstance(limitations, list)
                or not limitations
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in limitations
                )
            ):
                errors.append("official_limited_boundary_invalid")
        hashes = result.get("source_sha256s")
        sources = result.get("officialSources")
        if (
            not isinstance(hashes, list)
            or not hashes
            or any(
                not isinstance(item, str) or len(item) != 64 for item in hashes
            )
            or not isinstance(sources, list)
            or not sources
        ):
            errors.append("official_source_binding_invalid")
        else:
            source_hashes = set(hashes)
            seen: set[str] = set()
            sources_by_id: dict[str, Mapping[str, Any]] = {}
            for source in sources:
                source_id = source.get("sourceId") if isinstance(source, Mapping) else None
                body_sha256 = source.get("bodySha256") if isinstance(source, Mapping) else None
                if (
                    not isinstance(source, Mapping)
                    or not isinstance(source_id, str)
                    or not source_id
                    or source_id in seen
                    or source.get("authority") != "official_public_source"
                    or not isinstance(source.get("sourceAuthority"), str)
                    or not source["sourceAuthority"]
                    or not isinstance(source.get("requestedUrl"), str)
                    or not source["requestedUrl"].startswith("https://")
                    or not isinstance(source.get("finalUrl"), str)
                    or not source["finalUrl"].startswith("https://")
                    or not isinstance(source.get("accessedAt"), str)
                    or not source["accessedAt"]
                    or not isinstance(source.get("parseLocation"), str)
                    or not source["parseLocation"]
                    or not isinstance(body_sha256, str)
                    or body_sha256 not in source_hashes
                    or not isinstance(source.get("sourceVersion"), str)
                    or not source["sourceVersion"]
                    or source.get("snapshotVersion")
                    != f"sha256:{body_sha256}"
                    or not isinstance(source.get("receiptSha256"), str)
                    or len(source["receiptSha256"]) != 64
                ):
                    errors.append("official_source_binding_invalid")
                    break
                seen.add(source_id)
                sources_by_id[source_id] = source
            bindings = result.get("officialClaimBindings")
            used_source_ids: set[str] = set()
            if not isinstance(bindings, list) or not bindings:
                errors.append("official_claim_source_binding_invalid")
            else:
                for binding in bindings:
                    source_ids = (
                        binding.get("sourceIds")
                        if isinstance(binding, Mapping)
                        else None
                    )
                    claim_hashes = (
                        binding.get("sourceSha256s")
                        if isinstance(binding, Mapping)
                        else None
                    )
                    if (
                        not isinstance(binding, Mapping)
                        or not isinstance(binding.get("claimId"), str)
                        or re.fullmatch(
                            r"claim_[0-9a-f]{24}", str(binding.get("claimId"))
                        )
                        is None
                        or not isinstance(binding.get("statement"), str)
                        or not binding["statement"].strip()
                        or not isinstance(binding.get("modality"), str)
                        or not binding["modality"]
                        or not isinstance(source_ids, list)
                        or not source_ids
                        or len(source_ids) != len(set(source_ids))
                        or any(source_id not in sources_by_id for source_id in source_ids)
                        or not isinstance(claim_hashes, list)
                        or not claim_hashes
                        or len(claim_hashes) != len(set(claim_hashes))
                        or set(claim_hashes)
                        != {
                            str(sources_by_id[source_id].get("bodySha256"))
                            for source_id in source_ids
                        }
                    ):
                        errors.append("official_claim_source_binding_invalid")
                        break
                    used_source_ids.update(source_ids)
            if sources_by_id and used_source_ids != set(sources_by_id):
                errors.append("official_claim_source_binding_invalid")
        browser = observation.get("browser_view")
        request_research_id = (
            request.get("research_id") if isinstance(request, Mapping) else None
        )
        browser_text = (
            browser.get("visible_text") if isinstance(browser, Mapping) else None
        )
        browser_links = (
            browser.get("links") if isinstance(browser, Mapping) else None
        )
        page_url = browser.get("page_url") if isinstance(browser, Mapping) else None
        page_path = urlsplit(page_url).path if isinstance(page_url, str) else None
        if (
            not isinstance(browser, Mapping)
            or browser.get("contract") != "d18.browser_product_observation/v1"
            or browser.get("release_id") != release_id
            or browser.get("research_id") != request_research_id
            or browser.get("job_id") != observation.get("job_id")
            or page_path != f"/research/{request_research_id}"
            or not isinstance(browser_text, str)
            or not browser_text.strip()
            or not isinstance(browser_links, list)
            or len(browser_links) != len(set(browser_links))
            or any(not isinstance(link, str) for link in browser_links)
            or not _rendered_answer_fragments(answer)
            or any(
                fragment not in browser_text
                for fragment in _rendered_answer_fragments(answer)
            )
        ):
            errors.append("official_browser_result_invalid")
        else:
            projected_sources = result.get("officialSources")
            projected_claims = result.get("officialClaimBindings")
            if not isinstance(projected_sources, list) or not isinstance(
                projected_claims, list
            ):
                errors.append("official_browser_result_invalid")
            else:
                expected_text = [
                    str(value)
                    for source in projected_sources
                    if isinstance(source, Mapping)
                    for value in (
                        source.get("sourceId"),
                        source.get("sourceAuthority"),
                        source.get("sourceVersion"),
                        source.get("accessedAt"),
                        source.get("parseLocation"),
                        source.get("bodySha256"),
                    )
                ] + [
                    str(value)
                    for claim in projected_claims
                    if isinstance(claim, Mapping)
                    for value in (
                        claim.get("statement"),
                        *(claim.get("sourceIds") or []),
                    )
                ]
                expected_links = {
                    str(source.get("finalUrl"))
                    for source in projected_sources
                    if isinstance(source, Mapping)
                }
                if any(value not in browser_text for value in expected_text) or not (
                    expected_links <= set(browser_links)
                ):
                    errors.append("official_browser_result_invalid")
    execution = result_view.get("executionReceipt") if isinstance(result_view, Mapping) else None
    execution_counts = execution.get("counts") if isinstance(execution, Mapping) else None
    observed_external_reads = (
        execution_counts.get("external_read")
        if isinstance(execution_counts, Mapping)
        else None
    )
    errors.extend(
        _execution_receipt_errors(
            execution,
            prefix="official",
            expected_counts={
                "model": 2,
                "tool": 1,
                "external_read": observed_external_reads,
                "external_write": 0,
                "useless_retry": 0,
            },
            max_first_model_ms=OFFICIAL_MAX_FIRST_MODEL_MS,
            max_final_ms=OFFICIAL_MAX_FINAL_MS,
            require_run_control=True,
        )
    )
    if (
        isinstance(execution_counts, Mapping)
        and execution_counts.get("external_read") not in {0, 1}
    ):
        errors.append("official_external_read_count_invalid")
    retry = observation.get("retry")
    if not isinstance(retry, Mapping) or retry.get("same_job_id") is not True:
        errors.append("official_retry_job_identity_drifted")
    elif retry.get("result_sha256_before") != retry.get("result_sha256_after"):
        errors.append("official_retry_result_drifted")
    if isinstance(retry, Mapping):
        for label in (
            "execution_receipt_sha256",
            "project_record_sha256",
            "project_record_event_count",
            "sse_sha256",
        ):
            if retry.get(f"{label}_before") != retry.get(f"{label}_after"):
                errors.append(f"official_retry_{label}_drifted")
    receipt = {
        "contract": "d18.product_readiness_journey_receipt/v1",
        "journey": OFFICIAL_JOURNEY,
        "status": "passed" if not errors else "failed",
        "release_id": release_id,
        "errors": errors,
        "observation_sha256": canonical_sha256(observation),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def evaluate_missing_data_journey(
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate one waiting/resume journey from public, same-release evidence."""

    errors: list[str] = []
    if observation.get("contract") != "d18.product_readiness_observation/v1":
        errors.append("observation_contract_invalid")
    if observation.get("journey") != MISSING_DATA_JOURNEY:
        errors.append("journey_identity_invalid")
    release_id = _release_identity(observation, errors)
    request = observation.get("request")
    research_id = request.get("research_id") if isinstance(request, Mapping) else None
    job_id = observation.get("job_id")
    if (
        not isinstance(request, Mapping)
        or not isinstance(research_id, str)
        or request.get("material_ids") not in (None, [])
        or request.get("source_materials") not in (None, [])
        or not isinstance(job_id, str)
        or not job_id
    ):
        errors.append("missing_data_request_boundary_invalid")
    waiting_elapsed = observation.get("waiting_elapsed_ms")
    if (
        isinstance(waiting_elapsed, bool)
        or not isinstance(waiting_elapsed, int)
        or waiting_elapsed < 0
        or waiting_elapsed > MISSING_DATA_MAX_WAITING_MS
    ):
        errors.append("missing_data_waiting_budget_exceeded")
    wall_elapsed = observation.get("wall_elapsed_ms")
    if (
        isinstance(wall_elapsed, bool)
        or not isinstance(wall_elapsed, int)
        or wall_elapsed < 0
    ):
        errors.append("missing_data_elapsed_invalid")
    resume_elapsed = observation.get("resume_elapsed_ms")
    if (
        isinstance(resume_elapsed, bool)
        or not isinstance(resume_elapsed, int)
        or resume_elapsed < 0
        or resume_elapsed > MISSING_DATA_MAX_FINAL_MS
    ):
        errors.append("missing_data_final_budget_exceeded")
    waiting = observation.get("waiting_view")
    input_required = (
        waiting.get("input_required") if isinstance(waiting, Mapping) else None
    )
    missing_fields = (
        input_required.get("missing_fields")
        if isinstance(input_required, Mapping)
        else None
    )
    question = (
        input_required.get("question")
        if isinstance(input_required, Mapping)
        else None
    )
    if (
        not isinstance(waiting, Mapping)
        or waiting.get("job_id") != job_id
        or waiting.get("status") != "waiting_for_input"
        or not isinstance(input_required, Mapping)
        or input_required.get("interaction_kind")
        != "factual_data_availability"
        or input_required.get("authorization_required") is not False
        or not isinstance(missing_fields, list)
        or not missing_fields
        or missing_fields != sorted(set(missing_fields))
        or any(field not in MISSING_DATA_FIELD_LABELS for field in missing_fields)
    ):
        errors.append("missing_data_waiting_boundary_invalid")
        missing_fields = []
    if (
        not isinstance(question, str)
        or not question.strip()
        or not isinstance(input_required, Mapping)
        or input_required.get("question_contract")
        != "d18.missing_fact_question/v1"
        or input_required.get("question_count") != 1
        or any(
            MISSING_DATA_FIELD_LABELS[field] not in question
            for field in missing_fields
        )
    ):
        errors.append("missing_data_question_not_precise")
    supply = observation.get("supply")
    input_receipt = (
        supply.get("input_receipt") if isinstance(supply, Mapping) else None
    )
    unsigned_input_receipt = (
        {
            key: deepcopy(value)
            for key, value in input_receipt.items()
            if key != "receipt_sha256"
        }
        if isinstance(input_receipt, Mapping)
        else None
    )
    if (
        not isinstance(supply, Mapping)
        or supply.get("job_id") != job_id
        or not isinstance(input_receipt, Mapping)
        or input_receipt.get("contract") != "d18.public_job_input_receipt/v1"
        or input_receipt.get("kind") != "decision"
        or not isinstance(input_receipt.get("input_id"), str)
        or not isinstance(input_receipt.get("decision_id"), str)
        or input_receipt.get("decision") != "unavailable"
        or not isinstance(input_receipt.get("input_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", input_receipt["input_sha256"])
        is None
        or input_receipt.get("receipt_sha256")
        != canonical_sha256(unsigned_input_receipt)
    ):
        errors.append("missing_data_supply_boundary_invalid")
    browser_result = observation.get("browser_result")
    browser_interaction = (
        browser_result.get("interaction")
        if isinstance(browser_result, Mapping)
        else None
    )
    if (
        not isinstance(browser_interaction, Mapping)
        or browser_interaction.get("contract")
        != "d18.browser_input_interaction/v1"
        or browser_interaction.get("interaction_kind")
        != "factual_data_availability"
        or browser_interaction.get("decision") != "unavailable"
        or browser_interaction.get("before_status") != "waiting_for_input"
        or browser_interaction.get("after_status") not in {"bounded", "completed"}
        or not isinstance(input_receipt, Mapping)
        or browser_interaction.get("input_id") != input_receipt.get("input_id")
        or browser_interaction.get("decision_id")
        != input_receipt.get("decision_id")
        or browser_interaction.get("input_receipt_sha256")
        != input_receipt.get("receipt_sha256")
        or browser_interaction.get("submit_to_terminal_ms") != resume_elapsed
    ):
        errors.append("missing_data_browser_interaction_invalid")
    waiting_sequences = observation.get("waiting_sse_sequences")
    waiting_statuses = observation.get("waiting_sse_statuses")
    sequences = observation.get("sse_sequences")
    if (
        not isinstance(waiting_sequences, list)
        or not waiting_sequences
        or any(
            isinstance(item, bool) or not isinstance(item, int)
            for item in waiting_sequences
        )
        or waiting_sequences != sorted(set(waiting_sequences))
        or not isinstance(waiting_statuses, list)
        or "queued" not in waiting_statuses
        or "running" not in waiting_statuses
        or not waiting_statuses
        or waiting_statuses[-1] != "waiting_for_input"
        or not isinstance(sequences, list)
        or sequences[: len(waiting_sequences)] != waiting_sequences
    ):
        errors.append("missing_data_waiting_sse_invalid")
    if (
        not isinstance(sequences, list)
        or not sequences
        or any(isinstance(item, bool) or not isinstance(item, int) for item in sequences)
        or sequences != sorted(set(sequences))
    ):
        errors.append("sse_sequence_invalid")
    waiting_cursor = (
        waiting_sequences[-1]
        if isinstance(waiting_sequences, list) and waiting_sequences
        else None
    )
    waiting_prefix_length = (
        len(waiting_sequences) if isinstance(waiting_sequences, list) else 0
    )
    reconnect = observation.get("reconnect")
    reconnect_sequences = (
        reconnect.get("sequences") if isinstance(reconnect, Mapping) else None
    )
    reconnect_statuses = (
        reconnect.get("statuses") if isinstance(reconnect, Mapping) else None
    )
    if (
        not isinstance(reconnect, Mapping)
        or reconnect.get("after_sequence") != waiting_cursor
        or not isinstance(reconnect_sequences, list)
        or not isinstance(sequences, list)
        or reconnect_sequences != sequences[waiting_prefix_length:]
        or not reconnect_sequences
        or not isinstance(reconnect_statuses, list)
        or "queued" not in reconnect_statuses
        or "running" not in reconnect_statuses
        or reconnect_statuses[-1] not in {"bounded", "completed"}
    ):
        errors.append("missing_data_sse_reconnect_invalid")
    result_view = observation.get("result_view")
    result = result_view.get("result") if isinstance(result_view, Mapping) else None
    if not isinstance(result_view, Mapping) or result_view.get("status") != "accepted":
        errors.append("missing_data_result_not_accepted")
    if not isinstance(result, Mapping):
        errors.append("missing_data_result_projection_absent")
    else:
        answer = result.get("bounded_conclusion") or result.get("summary")
        if not isinstance(answer, str) or not answer.strip():
            errors.append("missing_data_answer_absent")
        if result.get("routeFamily") != "full_lca_workflow":
            errors.append("missing_data_route_family_invalid")
        if (
            result.get("status") != "formal_limited_result_accepted"
            or result.get("claim_ceiling") != "calculation_structure_only"
            or result.get("missing_fields") != missing_fields
            or not isinstance(result.get("limitations"), list)
            or not result["limitations"]
        ):
            errors.append("missing_data_limited_result_invalid")
    execution = (
        result_view.get("executionReceipt")
        if isinstance(result_view, Mapping)
        else None
    )
    waiting_execution = observation.get("waiting_execution_receipt")
    waiting_unsigned = (
        {
            key: deepcopy(value)
            for key, value in waiting_execution.items()
            if key != "receipt_sha256"
        }
        if isinstance(waiting_execution, Mapping)
        else None
    )
    waiting_counts = (
        waiting_execution.get("counts")
        if isinstance(waiting_execution, Mapping)
        else None
    )
    waiting_model_calls = (
        waiting_execution.get("model_calls")
        if isinstance(waiting_execution, Mapping)
        else None
    )
    if (
        not isinstance(waiting_execution, Mapping)
        or waiting_execution.get("contract")
        != "lca_research_kernel.public_execution_receipt/v1"
        or waiting_execution.get("receipt_sha256")
        != canonical_sha256(waiting_unsigned)
        or not isinstance(waiting_counts, Mapping)
        or waiting_counts.get("model") != 1
        or waiting_counts.get("tool") != 0
        or waiting_counts.get("external_read") != 0
        or waiting_counts.get("external_write") != 0
        or waiting_counts.get("useless_retry") != 0
        or not isinstance(waiting_model_calls, list)
        or len(waiting_model_calls) != 1
    ):
        errors.append("missing_data_waiting_execution_receipt_invalid")
    errors.extend(
        _execution_receipt_errors(
            execution,
            prefix="missing_data",
            expected_counts={
                "model": 1,
                "tool": 0,
                "external_read": 0,
                "external_write": 0,
                "useless_retry": 0,
            },
            max_first_model_ms=MISSING_DATA_MAX_WAITING_MS,
            max_final_ms=None,
        )
    )
    if (
        isinstance(execution, Mapping)
        and isinstance(waiting_execution, Mapping)
        and (
            execution.get("counts") != waiting_execution.get("counts")
            or execution.get("model_calls") != waiting_execution.get("model_calls")
            or execution.get("first_model_request_ms")
            != waiting_execution.get("first_model_request_ms")
        )
    ):
        errors.append("missing_data_resume_reexecuted_work")
    waiting_record = observation.get("waiting_project_record")
    final_record = (
        execution.get("project_record") if isinstance(execution, Mapping) else None
    )
    if (
        not isinstance(waiting_record, Mapping)
        or not isinstance(final_record, Mapping)
        or not isinstance(waiting_execution, Mapping)
        or waiting_record != waiting_execution.get("project_record")
        or isinstance(waiting_record.get("event_count"), bool)
        or not isinstance(waiting_record.get("event_count"), int)
        or isinstance(final_record.get("event_count"), bool)
        or not isinstance(final_record.get("event_count"), int)
        or final_record["event_count"] <= waiting_record["event_count"]
        or final_record["event_count"] - waiting_record["event_count"]
        > MISSING_DATA_MAX_EVENT_GROWTH
    ):
        errors.append("missing_data_event_growth_invalid")
    waiting_change_counts = (
        waiting_record.get("accepted_change_counts")
        if isinstance(waiting_record, Mapping)
        else None
    )
    final_change_counts = (
        final_record.get("accepted_change_counts")
        if isinstance(final_record, Mapping)
        else None
    )
    if (
        not isinstance(waiting_change_counts, Mapping)
        or not isinstance(final_change_counts, Mapping)
        or waiting_change_counts.get("formal_result") != 0
        or waiting_change_counts.get("gap_resolution") != 0
        or final_change_counts.get("formal_result") != 1
        or final_change_counts.get("gap_resolution") != len(missing_fields)
    ):
        errors.append("missing_data_accepted_event_counts_invalid")
    retry = observation.get("retry")
    if not isinstance(retry, Mapping) or retry.get("same_job_id") is not True:
        errors.append("missing_data_retry_job_identity_drifted")
    elif retry.get("result_sha256_before") != retry.get("result_sha256_after"):
        errors.append("missing_data_retry_result_drifted")
    if isinstance(retry, Mapping):
        for label in (
            "execution_receipt_sha256",
            "project_record_sha256",
            "project_record_event_count",
            "sse_sha256",
        ):
            if retry.get(f"{label}_before") != retry.get(f"{label}_after"):
                errors.append(f"missing_data_retry_{label}_drifted")
    for browser_key, expected_text in (
        ("browser_waiting", question),
        (
            "browser_result",
            (
                result.get("bounded_conclusion") or result.get("summary")
                if isinstance(result, Mapping)
                else None
            ),
        ),
    ):
        browser = observation.get(browser_key)
        visible_text = (
            browser.get("visible_text") if isinstance(browser, Mapping) else None
        )
        page_url = browser.get("page_url") if isinstance(browser, Mapping) else None
        page_path = urlsplit(page_url).path if isinstance(page_url, str) else None
        if (
            not isinstance(browser, Mapping)
            or browser.get("contract") != "d18.browser_product_observation/v1"
            or browser.get("release_id") != release_id
            or browser.get("research_id") != research_id
            or browser.get("job_id") != job_id
            or page_path != f"/research/{research_id}"
            or not isinstance(visible_text, str)
            or not isinstance(expected_text, str)
            or expected_text not in visible_text
        ):
            errors.append(f"missing_data_{browser_key}_invalid")
    browser_result_text = (
        browser_result.get("visible_text")
        if isinstance(browser_result, Mapping)
        else ""
    )
    if isinstance(browser_result_text, str) and any(
        field not in browser_result_text for field in missing_fields
    ):
        errors.append("missing_data_browser_result_invalid")
    receipt = {
        "contract": "d18.product_readiness_journey_receipt/v1",
        "journey": MISSING_DATA_JOURNEY,
        "status": "passed" if not errors else "failed",
        "release_id": release_id,
        "errors": errors,
        "observation_sha256": canonical_sha256(observation),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def evaluate_native_journey(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate exact TianGong identity without confusing a tool with science."""

    errors: list[str] = []
    if observation.get("contract") != "d18.product_readiness_observation/v1":
        errors.append("observation_contract_invalid")
    if observation.get("journey") != NATIVE_JOURNEY:
        errors.append("journey_identity_invalid")
    release_id = _release_identity(observation, errors)
    request = observation.get("request")
    research_id = request.get("research_id") if isinstance(request, Mapping) else None
    job_id = observation.get("job_id")
    if (
        not isinstance(request, Mapping)
        or not isinstance(research_id, str)
        or not research_id
        or request.get("material_ids") not in (None, [])
        or request.get("source_materials") not in (None, [])
        or not isinstance(job_id, str)
        or not job_id
    ):
        errors.append("native_request_boundary_invalid")
    wall_elapsed = observation.get("wall_elapsed_ms")
    if (
        isinstance(wall_elapsed, bool)
        or not isinstance(wall_elapsed, int)
        or wall_elapsed < 0
        or wall_elapsed > NATIVE_MAX_FINAL_MS
    ):
        errors.append("native_wall_clock_budget_exceeded")
    sequences = observation.get("sse_sequences")
    if (
        not isinstance(sequences, list)
        or not sequences
        or any(isinstance(item, bool) or not isinstance(item, int) for item in sequences)
        or sequences != sorted(set(sequences))
    ):
        errors.append("sse_sequence_invalid")
    result_view = observation.get("result_view")
    result = result_view.get("result") if isinstance(result_view, Mapping) else None
    if not isinstance(result_view, Mapping) or result_view.get("status") != "accepted":
        errors.append("native_result_not_accepted")
    if not isinstance(result, Mapping):
        errors.append("native_result_projection_absent")
        answer = None
        authority = None
        boundary = None
    else:
        answer = result.get("bounded_conclusion") or result.get("summary")
        authority = result.get("nativeAuthority")
        boundary = result.get("scientificBoundary")
        if (
            result.get("status") != "formal_limited_result_accepted"
            or result.get("claim_ceiling") != "capability_contract_only"
            or result.get("routeFamily") != "full_lca_workflow"
            or not isinstance(answer, str)
            or not answer.strip()
            or not isinstance(result.get("limitations"), list)
            or not result["limitations"]
        ):
            errors.append("native_limited_result_invalid")
        if (
            not isinstance(authority, Mapping)
            or authority.get("provider") != "TianGong"
            or authority.get("capabilityId") != "tiangong.native.tidas-sdk"
            or authority.get("sourceIdentity") != "@tiangong-lca/tidas-sdk"
            or authority.get("sourceVersion") != "0.1.45"
            or not isinstance(authority.get("sourceIntegrity"), str)
            or not authority["sourceIntegrity"].startswith("sha512-")
            or not isinstance(authority.get("contentSha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", authority["contentSha256"]) is None
        ):
            errors.append("native_authority_invalid")
        if boundary != {
            "lcaMethod": "not_selected",
            "exchangeProvider": "not_selected",
            "quantityUnit": "not_evaluated",
            "scientificResultCreated": False,
        }:
            errors.append("native_scientific_boundary_invalid")
    execution = (
        result_view.get("executionReceipt")
        if isinstance(result_view, Mapping)
        else None
    )
    errors.extend(
        _execution_receipt_errors(
            execution,
            prefix="native",
            expected_counts={
                "model": 1,
                "tool": 1,
                "external_read": 0,
                "external_write": 0,
                "useless_retry": 0,
            },
            max_first_model_ms=NATIVE_MAX_FIRST_MODEL_MS,
            max_final_ms=NATIVE_MAX_FINAL_MS,
        )
    )
    retry = observation.get("retry")
    if not isinstance(retry, Mapping) or retry.get("same_job_id") is not True:
        errors.append("native_retry_job_identity_drifted")
    elif retry.get("result_sha256_before") != retry.get("result_sha256_after"):
        errors.append("native_retry_result_drifted")
    if isinstance(retry, Mapping):
        for label in (
            "execution_receipt_sha256",
            "project_record_sha256",
            "project_record_event_count",
            "sse_sha256",
        ):
            if retry.get(f"{label}_before") != retry.get(f"{label}_after"):
                errors.append(f"native_retry_{label}_drifted")
    browser = observation.get("browser_view")
    visible_text = browser.get("visible_text") if isinstance(browser, Mapping) else None
    page_url = browser.get("page_url") if isinstance(browser, Mapping) else None
    page_path = urlsplit(page_url).path if isinstance(page_url, str) else None
    expected_visible = [
        answer,
        "tiangong.native.tidas-sdk",
        "@tiangong-lca/tidas-sdk",
        "0.1.45",
        "LCA 方法：未选择",
        "Provider：未选择",
        "单位：未评价",
        "未生成科学计算结果",
        authority.get("contentSha256") if isinstance(authority, Mapping) else None,
    ]
    if (
        not isinstance(browser, Mapping)
        or browser.get("contract") != "d18.browser_product_observation/v1"
        or browser.get("release_id") != release_id
        or browser.get("research_id") != research_id
        or browser.get("job_id") != job_id
        or page_path != f"/research/{research_id}"
        or not isinstance(visible_text, str)
        or any(
            not isinstance(value, str) or not value or value not in visible_text
            for value in expected_visible
        )
    ):
        errors.append("native_browser_result_invalid")
    receipt = {
        "contract": "d18.product_readiness_journey_receipt/v1",
        "journey": NATIVE_JOURNEY,
        "status": "passed" if not errors else "failed",
        "release_id": release_id,
        "errors": errors,
        "observation_sha256": canonical_sha256(observation),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def _json_bytes_match(payload: object, raw: object) -> bool:
    if not isinstance(payload, Mapping) or not isinstance(raw, bytes):
        return False
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(decoded, dict) and decoded == dict(payload)


def _controlled_browser_capture_errors(
    evidence: object,
    *,
    observation: Mapping[str, Any],
    browser_key: str,
    stage: str,
    verifier: Callable[[bytes, str], bool] | None,
) -> list[str]:
    if not isinstance(evidence, Mapping):
        return ["controlled_browser_capture_invalid"]
    capture = evidence.get("capture")
    capture_bytes = evidence.get("capture_bytes")
    screenshot_bytes = evidence.get("screenshot_bytes")
    browser = observation.get(browser_key)
    if not isinstance(capture, Mapping) or not isinstance(browser, Mapping):
        return ["controlled_browser_capture_invalid"]
    unsigned = {
        key: deepcopy(value)
        for key, value in capture.items()
        if key != "capture_sha256"
    }
    runtime = capture.get("browser_runtime")
    screenshot = capture.get("screenshot")
    captured_at = capture.get("captured_at")
    authentication = capture.get("authentication")
    evidence_unsigned = {
        key: deepcopy(value)
        for key, value in capture.items()
        if key not in {"authentication", "capture_sha256"}
    }
    evidence_sha256 = canonical_sha256(evidence_unsigned)
    gate_run_id = (
        authentication.get("gate_run_id")
        if isinstance(authentication, Mapping)
        else None
    )
    try:
        authentication_message = browser_capture_authentication_message(
            evidence_sha256=evidence_sha256,
            gate_run_id=gate_run_id if isinstance(gate_run_id, str) else "",
        )
    except ValueError:
        authentication_message = b""
    dom_snapshot = {
        "visible_text": browser.get("visible_text"),
        "links": browser.get("links", []),
        "page_url": browser.get("page_url"),
        "title": runtime.get("title") if isinstance(runtime, Mapping) else None,
    }
    if (
        capture.get("contract") != "d18.controlled_browser_capture/v1"
        or capture.get("harness") != "ego-lite/cdp"
        or capture.get("capture_method") != "runtime_dom_and_png"
        or not isinstance(captured_at, str)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", captured_at)
        is None
        or capture.get("journey") != observation.get("journey")
        or capture.get("stage") != stage
        or capture.get("release_id") != browser.get("release_id")
        or capture.get("research_id") != browser.get("research_id")
        or capture.get("job_id") != browser.get("job_id")
        or capture.get("page_url") != browser.get("page_url")
        or capture.get("browser_view") != browser
        or capture.get("dom_visible_text_sha256")
        != canonical_sha256(browser.get("visible_text"))
        or capture.get("dom_links_sha256")
        != canonical_sha256(browser.get("links", []))
        or capture.get("dom_snapshot_sha256") != canonical_sha256(dom_snapshot)
        or capture.get("capture_sha256") != canonical_sha256(unsigned)
        or not isinstance(authentication, Mapping)
        or authentication.get("contract")
        != "d18.browser_capture_authentication/v1"
        or authentication.get("algorithm") != "hmac-sha256"
        or not isinstance(authentication.get("gate_run_id"), str)
        or not authentication["gate_run_id"]
        or authentication.get("evidence_sha256") != evidence_sha256
        or not isinstance(authentication.get("tag"), str)
        or verifier is None
        or not verifier(authentication_message, str(authentication.get("tag")))
        or not _json_bytes_match(capture, capture_bytes)
        or not isinstance(runtime, Mapping)
        or not isinstance(runtime.get("user_agent"), str)
        or not runtime["user_agent"].strip()
        or runtime.get("document_ready_state") != "complete"
        or isinstance(runtime.get("viewport_width"), bool)
        or not isinstance(runtime.get("viewport_width"), int)
        or runtime["viewport_width"] <= 0
        or isinstance(runtime.get("viewport_height"), bool)
        or not isinstance(runtime.get("viewport_height"), int)
        or runtime["viewport_height"] <= 0
        or not isinstance(runtime.get("title"), str)
        or not runtime["title"].strip()
        or not isinstance(screenshot, Mapping)
        or screenshot.get("media_type") != "image/png"
        or not isinstance(screenshot_bytes, bytes)
        or not screenshot_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        or screenshot.get("byte_size") != len(screenshot_bytes)
        or screenshot.get("sha256")
        != hashlib.sha256(screenshot_bytes).hexdigest()
    ):
        return ["controlled_browser_capture_invalid"]
    return []


def _complete_release_identity(
    observation: Mapping[str, Any],
) -> dict[str, Any] | None:
    required = {
        "contract",
        "release_id",
        "manifest_sha256",
        "backend_commit",
        "frontend_build_id",
    }
    identities = observation.get("release_identities")
    result_view = observation.get("result_view")
    runtime_release = (
        result_view.get("runtime_release")
        if isinstance(result_view, Mapping)
        else None
    )
    rows = [*identities, runtime_release] if isinstance(identities, list) else []
    normalized = [
        {key: row.get(key) for key in sorted(required)}
        for row in rows
        if isinstance(row, Mapping) and required <= set(row)
    ]
    if len(normalized) != len(rows) or not normalized:
        return None
    if len({canonical_sha256(row) for row in normalized}) != 1:
        return None
    return normalized[0]


def evaluate_product_readiness_release(
    packages: list[Mapping[str, Any]],
    *,
    browser_capture_verifier: Callable[[bytes, str], bool] | None = None,
) -> dict[str, Any]:
    """Accept one exact release only when all five real product journeys pass."""

    evaluators = {
        DIRECT_JOURNEY: evaluate_direct_journey,
        MATERIAL_JOURNEY: evaluate_material_journey,
        OFFICIAL_JOURNEY: evaluate_official_journey,
        MISSING_DATA_JOURNEY: evaluate_missing_data_journey,
        NATIVE_JOURNEY: evaluate_native_journey,
    }
    errors: list[str] = []
    package_by_journey: dict[str, Mapping[str, Any]] = {}
    for package in packages:
        observation = package.get("observation") if isinstance(package, Mapping) else None
        journey = observation.get("journey") if isinstance(observation, Mapping) else None
        if not isinstance(journey, str) or journey in package_by_journey:
            errors.append("release_journey_set_invalid")
            continue
        package_by_journey[journey] = package
    if set(package_by_journey) != PRODUCT_READINESS_JOURNEYS:
        errors.append("release_journey_set_invalid")

    release_ids: set[str] = set()
    release_identities: dict[str, dict[str, Any]] = {}
    release_manifests: dict[str, dict[str, Any]] = {}
    gate_run_ids: set[str] = set()
    accepted: list[dict[str, Any]] = []
    for journey in sorted(PRODUCT_READINESS_JOURNEYS):
        package = package_by_journey.get(journey)
        if not isinstance(package, Mapping):
            continue
        observation = package.get("observation")
        receipt = package.get("receipt")
        if not isinstance(observation, Mapping) or not isinstance(receipt, Mapping):
            errors.append("release_evidence_file_invalid")
            continue
        if (
            not _json_bytes_match(observation, package.get("observation_bytes"))
            or not _json_bytes_match(receipt, package.get("receipt_bytes"))
        ):
            errors.append("release_evidence_file_invalid")
        try:
            product_evidence = validate_service_product_evidence(
                observation.get("result_view", {}).get("productEvidence")
                if isinstance(observation.get("result_view"), Mapping)
                else None,
                job_id=observation.get("job_id"),
                result_view=observation.get("result_view"),
                verifier=browser_capture_verifier,
            )
        except (TypeError, ValueError):
            product_evidence = None
            errors.append("service_product_evidence_invalid")
        release_roles = (
            product_evidence.get("release_roles")
            if isinstance(product_evidence, Mapping)
            else None
        )
        evidence_release = (
            product_evidence.get("release_identity")
            if isinstance(product_evidence, Mapping)
            else None
        )
        evidence_manifest = (
            product_evidence.get("release_manifest")
            if isinstance(product_evidence, Mapping)
            else None
        )
        if isinstance(evidence_manifest, Mapping):
            normalized_manifest = deepcopy(dict(evidence_manifest))
            release_manifests[
                canonical_sha256(normalized_manifest)
            ] = normalized_manifest
        else:
            errors.append("release_manifest_evidence_invalid")
        if (
            not isinstance(release_roles, list)
            or len(release_roles) != len(PRODUCT_RELEASE_ROLES)
            or {
                row.get("role")
                for row in release_roles
                if isinstance(row, Mapping)
            }
            != PRODUCT_RELEASE_ROLES
            or any(
                not isinstance(row, Mapping)
                or set(row) != {"role", "identity"}
                or row.get("identity") != evidence_release
                for row in release_roles
            )
        ):
            errors.append("release_role_evidence_invalid")
        retry = observation.get("retry")
        try:
            replay_evidence = validate_service_idempotency_replay_evidence(
                retry.get("idempotencyReplayEvidence")
                if isinstance(retry, Mapping)
                else None,
                job_id=observation.get("job_id"),
                verifier=browser_capture_verifier,
            )
        except (TypeError, ValueError):
            replay_evidence = None
            errors.append("service_idempotency_replay_evidence_invalid")
        if (
            not isinstance(replay_evidence, Mapping)
            or replay_evidence.get("release_identity") != evidence_release
            or replay_evidence.get("research_id")
            != product_evidence.get("research_id")
        ):
            errors.append("service_idempotency_replay_evidence_drifted")
        if journey == MATERIAL_JOURNEY:
            material_rows = (
                product_evidence.get("materials")
                if isinstance(product_evidence, Mapping)
                else None
            )
            source = observation.get("source")
            if (
                not isinstance(material_rows, list)
                or len(material_rows) != 1
                or not isinstance(material_rows[0], Mapping)
                or not isinstance(source, Mapping)
                or material_rows[0].get("input_sha256") != source.get("sha256")
                or material_rows[0].get("input_bytes") != source.get("bytes")
                or material_rows[0].get("qualified_locator_count")
                != MATERIAL_QUALIFIED_LOCATOR_COUNT
            ):
                errors.append("material_qualified_locator_count_unproven")
        evaluated = evaluators[journey](observation)
        if evaluated != dict(receipt) or evaluated.get("status") != "passed":
            errors.append("release_journey_not_passed")
            child_errors = evaluated.get("errors")
            if isinstance(child_errors, list):
                errors.extend(str(error) for error in child_errors)
        release_id = evaluated.get("release_id")
        if isinstance(release_id, str):
            release_ids.add(release_id)
        complete_identity = _complete_release_identity(observation)
        if complete_identity is None:
            errors.append("release_identity_mismatch")
        else:
            release_identities[canonical_sha256(complete_identity)] = complete_identity

        expected_captures = (
            [("browser_waiting", "waiting"), ("browser_result", "result")]
            if journey == MISSING_DATA_JOURNEY
            else [("browser_view", "result")]
        )
        capture_evidence = package.get("browser_captures")
        if not isinstance(capture_evidence, list) or len(capture_evidence) != len(
            expected_captures
        ):
            errors.append("controlled_browser_capture_invalid")
            capture_evidence = []
        capture_refs: list[dict[str, Any]] = []
        for index, (browser_key, stage) in enumerate(expected_captures):
            evidence = capture_evidence[index] if index < len(capture_evidence) else None
            errors.extend(
                _controlled_browser_capture_errors(
                    evidence,
                    observation=observation,
                    browser_key=browser_key,
                    stage=stage,
                    verifier=browser_capture_verifier,
                )
            )
            if isinstance(evidence, Mapping):
                capture = evidence.get("capture")
                screenshot_bytes = evidence.get("screenshot_bytes")
                if isinstance(capture, Mapping) and isinstance(screenshot_bytes, bytes):
                    authentication = capture.get("authentication")
                    if isinstance(authentication, Mapping) and isinstance(
                        authentication.get("gate_run_id"), str
                    ):
                        gate_run_ids.add(str(authentication["gate_run_id"]))
                    capture_refs.append(
                        {
                            "stage": stage,
                            "capture_sha256": capture.get("capture_sha256"),
                            "capture_file_sha256": hashlib.sha256(
                                evidence.get("capture_bytes", b"")
                            ).hexdigest(),
                            "screenshot_sha256": hashlib.sha256(
                                screenshot_bytes
                            ).hexdigest(),
                        }
                    )
        accepted.append(
            {
                "journey": journey,
                "research_id": (
                    observation.get("request", {}).get("research_id")
                    if isinstance(observation.get("request"), Mapping)
                    else None
                ),
                "job_id": observation.get("job_id"),
                "observation_sha256": canonical_sha256(observation),
                "observation_file_sha256": hashlib.sha256(
                    package.get("observation_bytes", b"")
                ).hexdigest(),
                "journey_receipt_sha256": receipt.get("receipt_sha256"),
                "journey_receipt_file_sha256": hashlib.sha256(
                    package.get("receipt_bytes", b"")
                ).hexdigest(),
                "browser_captures": capture_refs,
            }
        )
    if (
        len(release_ids) != 1
        or len(release_identities) != 1
        or len(release_manifests) != 1
    ):
        errors.append("release_identity_mismatch")
    if len(gate_run_ids) != 1:
        errors.append("controlled_browser_capture_invalid")
    errors = list(dict.fromkeys(errors))
    aggregate = {
        "contract": "d18.product_readiness_release_receipt/v1",
        "status": "passed" if not errors else "failed",
        "release_id": next(iter(release_ids)) if len(release_ids) == 1 else None,
        "release_identity": (
            next(iter(release_identities.values()))
            if len(release_identities) == 1
            else None
        ),
        "runtime_release_manifest": (
            next(iter(release_manifests.values()))
            if len(release_manifests) == 1
            else None
        ),
        "browser_gate_run_id": (
            next(iter(gate_run_ids)) if len(gate_run_ids) == 1 else None
        ),
        "journeys": accepted,
        "errors": errors,
    }
    aggregate["receipt_sha256"] = canonical_sha256(aggregate)
    return aggregate
