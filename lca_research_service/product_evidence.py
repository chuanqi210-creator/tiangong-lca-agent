"""Service-authenticated evidence for black-box Product Readiness."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Callable, Mapping

from .release_manifest import (
    runtime_release_binding,
    validate_runtime_release_manifest_evidence,
)


CONTRACT = "d18.service_product_evidence/v1"
AUTH_CONTRACT = "d18.service_product_evidence_authentication/v1"
REPLAY_CONTRACT = "d18.service_idempotency_replay_evidence/v1"
REPLAY_AUTH_CONTRACT = "d18.service_idempotency_replay_authentication/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def authentication_message(
    *, evidence_sha256: str, release_id: str, research_id: str, job_id: str
) -> bytes:
    if _SHA256.fullmatch(evidence_sha256) is None or _SHA256.fullmatch(release_id) is None:
        raise ValueError("service product evidence identity is invalid")
    if not research_id or not job_id:
        raise ValueError("service product evidence request identity is invalid")
    return b"\0".join(
        (
            b"d18.service-product-evidence/v1",
            release_id.encode("ascii"),
            research_id.encode("utf-8"),
            job_id.encode("utf-8"),
            evidence_sha256.encode("ascii"),
        )
    )


def replay_authentication_message(
    *, evidence_sha256: str, release_id: str, research_id: str, job_id: str
) -> bytes:
    if _SHA256.fullmatch(evidence_sha256) is None or _SHA256.fullmatch(release_id) is None:
        raise ValueError("service replay evidence identity is invalid")
    if not research_id or not job_id:
        raise ValueError("service replay request identity is invalid")
    return b"\0".join(
        (
            b"d18.service-idempotency-replay-evidence/v1",
            release_id.encode("ascii"),
            research_id.encode("utf-8"),
            job_id.encode("utf-8"),
            evidence_sha256.encode("ascii"),
        )
    )


def _accepted_material_rows(
    *,
    result: Mapping[str, Any],
    project_view: Mapping[str, Any],
    material_detail: Callable[[str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    source_use = result.get("sourceUse", [])
    if not isinstance(source_use, list):
        raise ValueError("service product material evidence is invalid")
    if not source_use:
        return []
    materials = project_view.get("materials")
    revisions = project_view.get("material_revisions")
    locators = project_view.get("evidence_locators")
    if (
        not isinstance(materials, Mapping)
        or not isinstance(revisions, Mapping)
        or not isinstance(locators, Mapping)
    ):
        raise ValueError("service product material evidence is invalid")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source in source_use:
        if not isinstance(source, Mapping):
            raise ValueError("service product material evidence is invalid")
        material_id = source.get("materialId")
        revision_id = source.get("revisionId")
        identity = (str(material_id), str(revision_id))
        if (
            not isinstance(material_id, str)
            or not material_id
            or not isinstance(revision_id, str)
            or not revision_id
            or identity in seen
        ):
            raise ValueError("service product material evidence is invalid")
        seen.add(identity)
        material = materials.get(material_id)
        revision = revisions.get(revision_id)
        detail = material_detail(material_id)
        detail_material = detail.get("material") if isinstance(detail, Mapping) else None
        detail_revisions = detail.get("revisions") if isinstance(detail, Mapping) else None
        detail_revision = next(
            (
                row
                for row in detail_revisions
                if isinstance(row, Mapping) and row.get("revision_id") == revision_id
            ),
            None,
        ) if isinstance(detail_revisions, list) else None
        artifact_ref = revision.get("artifact_ref") if isinstance(revision, Mapping) else None
        if (
            not isinstance(material, Mapping)
            or material.get("material_id") != material_id
            or not isinstance(revision, Mapping)
            or revision.get("material_id") != material_id
            or material.get("current_revision_id") != revision_id
            or not isinstance(detail_material, Mapping)
            or detail_material.get("material_id") != material_id
            or detail_material.get("current_revision_id") != revision_id
            or not isinstance(detail_revision, Mapping)
            or detail_revision.get("input_artifact_id")
            != (artifact_ref.get("sha256") if isinstance(artifact_ref, Mapping) else None)
        ):
            raise ValueError("service product material authority drifted")
        input_sha256 = artifact_ref.get("sha256") if isinstance(artifact_ref, Mapping) else None
        byte_size = detail_material.get("byte_size")
        receipt_sha256 = revision.get("processing_receipt_sha256")
        qualified_locator_count = sum(
            1
            for locator in locators.values()
            if isinstance(locator, Mapping)
            and locator.get("revision_id") == revision_id
            and locator.get("scientific_status") == "accepted_evidence"
        )
        if (
            not isinstance(input_sha256, str)
            or _SHA256.fullmatch(input_sha256) is None
            or isinstance(byte_size, bool)
            or not isinstance(byte_size, int)
            or byte_size < 1
            or not isinstance(receipt_sha256, str)
            or _SHA256.fullmatch(receipt_sha256) is None
            or qualified_locator_count < 1
        ):
            raise ValueError("service product material evidence is incomplete")
        rows.append(
            {
                "material_id": material_id,
                "revision_id": revision_id,
                "input_sha256": input_sha256,
                "input_bytes": byte_size,
                "processing_receipt_sha256": receipt_sha256,
                "qualified_locator_count": qualified_locator_count,
            }
        )
    return sorted(rows, key=lambda row: (row["material_id"], row["revision_id"]))


def build_service_product_evidence(
    *,
    job_id: str,
    release_manifest: Mapping[str, Any],
    result: Mapping[str, Any],
    execution_receipt: Mapping[str, Any],
    project_view: Mapping[str, Any],
    job_request_release: Mapping[str, Any],
    material_detail: Callable[[str], Mapping[str, Any]],
    signer: Callable[[bytes], str],
) -> dict[str, Any]:
    """Bind one accepted public result to service-owned evidence in one operation."""

    manifest = validate_runtime_release_manifest_evidence(release_manifest)
    release = runtime_release_binding(manifest)
    research_id = project_view.get("project_id")
    if not isinstance(job_id, str) or not job_id or not isinstance(research_id, str) or not research_id:
        raise ValueError("service product evidence request identity is invalid")
    if dict(job_request_release) != release:
        raise ValueError("service product job release drifted")
    receipt_unsigned = {
        key: deepcopy(value)
        for key, value in execution_receipt.items()
        if key != "receipt_sha256"
    }
    if execution_receipt.get("receipt_sha256") != canonical_sha256(receipt_unsigned):
        raise ValueError("service product execution receipt drifted")
    project_record = execution_receipt.get("project_record")
    if (
        not isinstance(project_record, Mapping)
        or project_record.get("last_event_sha256") != project_view.get("last_event_sha256")
    ):
        raise ValueError("service product Project Record receipt drifted")
    official_sources = result.get("officialSources", [])
    if not isinstance(official_sources, list) or any(
        not isinstance(source, Mapping) for source in official_sources
    ):
        raise ValueError("service product official-source evidence is invalid")
    unsigned = {
        "contract": CONTRACT,
        "job_id": job_id,
        "research_id": research_id,
        "release_manifest": deepcopy(manifest),
        "release_identity": release,
        "release_roles": [
            {"role": "backend_service", "identity": deepcopy(release)},
            {"role": "accepted_job_request", "identity": deepcopy(release)},
            {"role": "worker_execution", "identity": deepcopy(release)},
            {"role": "result_projection", "identity": deepcopy(release)},
        ],
        "result_sha256": canonical_sha256(result),
        "execution_receipt_sha256": execution_receipt["receipt_sha256"],
        "project_record": {
            "last_event_sha256": project_view["last_event_sha256"],
            "event_count": project_record.get("event_count"),
        },
        "materials": _accepted_material_rows(
            result=result,
            project_view=project_view,
            material_detail=material_detail,
        ),
        "official_sources": deepcopy(official_sources),
    }
    evidence_sha256 = canonical_sha256(unsigned)
    message = authentication_message(
        evidence_sha256=evidence_sha256,
        release_id=release["release_id"],
        research_id=research_id,
        job_id=job_id,
    )
    return {
        **unsigned,
        "authentication": {
            "contract": AUTH_CONTRACT,
            "algorithm": "hmac-sha256",
            "evidence_sha256": evidence_sha256,
            "tag": signer(message),
        },
    }


def build_service_idempotency_replay_evidence(
    *,
    job: Mapping[str, Any],
    release_manifest: Mapping[str, Any],
    signer: Callable[[bytes], str],
) -> dict[str, Any]:
    """Authenticate that the service observed an exact idempotent job replay."""

    manifest = validate_runtime_release_manifest_evidence(release_manifest)
    release = runtime_release_binding(manifest)
    job_id = job.get("job_id")
    research_id = job.get("project_id")
    request_sha256 = job.get("request_sha256")
    request_ref = job.get("request_ref")
    request_ref_sha256 = (
        request_ref.get("sha256") if isinstance(request_ref, Mapping) else None
    )
    idempotency_key = job.get("idempotency_key")
    if (
        not isinstance(job_id, str)
        or not job_id
        or not isinstance(research_id, str)
        or not research_id
        or not isinstance(request_sha256, str)
        or _SHA256.fullmatch(request_sha256) is None
        or not isinstance(request_ref_sha256, str)
        or _SHA256.fullmatch(request_ref_sha256) is None
        or not isinstance(idempotency_key, str)
        or not idempotency_key
    ):
        raise ValueError("service replay evidence is incomplete")
    unsigned = {
        "contract": REPLAY_CONTRACT,
        "job_id": job_id,
        "research_id": research_id,
        "request_sha256": request_sha256,
        "request_ref_sha256": request_ref_sha256,
        "idempotency_key_sha256": hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest(),
        "release_identity": release,
    }
    evidence_sha256 = canonical_sha256(unsigned)
    return {
        **unsigned,
        "authentication": {
            "contract": REPLAY_AUTH_CONTRACT,
            "algorithm": "hmac-sha256",
            "evidence_sha256": evidence_sha256,
            "tag": signer(
                replay_authentication_message(
                    evidence_sha256=evidence_sha256,
                    release_id=release["release_id"],
                    research_id=research_id,
                    job_id=job_id,
                )
            ),
        },
    }


def validate_service_idempotency_replay_evidence(
    value: object,
    *,
    job_id: object,
    verifier: Callable[[bytes, str], bool] | None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("service replay evidence is unavailable")
    evidence = deepcopy(dict(value))
    authentication = evidence.pop("authentication", None)
    if (
        set(evidence)
        != {
            "contract",
            "job_id",
            "research_id",
            "request_sha256",
            "request_ref_sha256",
            "idempotency_key_sha256",
            "release_identity",
        }
        or evidence.get("contract") != REPLAY_CONTRACT
        or evidence.get("job_id") != job_id
        or not all(
            _SHA256.fullmatch(str(evidence.get(key, ""))) is not None
            for key in (
                "request_sha256",
                "request_ref_sha256",
                "idempotency_key_sha256",
            )
        )
        or not isinstance(authentication, Mapping)
        or authentication.get("contract") != REPLAY_AUTH_CONTRACT
        or authentication.get("algorithm") != "hmac-sha256"
        or verifier is None
    ):
        raise ValueError("service replay evidence is invalid")
    release = evidence.get("release_identity")
    evidence_sha256 = canonical_sha256(evidence)
    tag = authentication.get("tag")
    if (
        not isinstance(release, Mapping)
        or _SHA256.fullmatch(str(release.get("release_id", ""))) is None
        or authentication.get("evidence_sha256") != evidence_sha256
        or not isinstance(tag, str)
        or not verifier(
            replay_authentication_message(
                evidence_sha256=evidence_sha256,
                release_id=str(release.get("release_id", "")),
                research_id=str(evidence.get("research_id", "")),
                job_id=str(job_id or ""),
            ),
            tag,
        )
    ):
        raise ValueError("service replay evidence authentication failed")
    return {**evidence, "authentication": deepcopy(dict(authentication))}


def validate_service_product_evidence(
    value: object,
    *,
    job_id: object,
    result_view: object,
    verifier: Callable[[bytes, str], bool] | None,
) -> dict[str, Any]:
    """Verify one service-authenticated result without reading service internals."""

    if not isinstance(value, Mapping) or not isinstance(result_view, Mapping):
        raise ValueError("service product evidence is unavailable")
    evidence = deepcopy(dict(value))
    authentication = evidence.pop("authentication", None)
    result = result_view.get("result")
    execution = result_view.get("executionReceipt")
    runtime_release = result_view.get("runtime_release")
    if (
        set(evidence) != {
            "contract", "job_id", "research_id", "release_manifest",
            "release_identity", "release_roles", "result_sha256", "execution_receipt_sha256",
            "project_record", "materials", "official_sources",
        }
        or evidence.get("contract") != CONTRACT
        or evidence.get("job_id") != job_id
        or not isinstance(result, Mapping)
        or not isinstance(execution, Mapping)
        or not isinstance(runtime_release, Mapping)
        or not isinstance(authentication, Mapping)
        or authentication.get("contract") != AUTH_CONTRACT
        or authentication.get("algorithm") != "hmac-sha256"
        or verifier is None
    ):
        raise ValueError("service product evidence is invalid")
    manifest = validate_runtime_release_manifest_evidence(evidence["release_manifest"])
    release = runtime_release_binding(manifest)
    release_roles = evidence.get("release_roles")
    evidence_sha256 = canonical_sha256(evidence)
    tag = authentication.get("tag")
    if (
        evidence.get("release_identity") != release
        or not isinstance(release_roles, list)
        or release_roles
        != [
            {"role": "backend_service", "identity": release},
            {"role": "accepted_job_request", "identity": release},
            {"role": "worker_execution", "identity": release},
            {"role": "result_projection", "identity": release},
        ]
        or dict(runtime_release) != release
        or evidence.get("result_sha256") != canonical_sha256(result)
        or evidence.get("execution_receipt_sha256") != execution.get("receipt_sha256")
        or authentication.get("evidence_sha256") != evidence_sha256
        or not isinstance(tag, str)
        or not verifier(
            authentication_message(
                evidence_sha256=evidence_sha256,
                release_id=release["release_id"],
                research_id=str(evidence.get("research_id", "")),
                job_id=str(job_id or ""),
            ),
            tag,
        )
    ):
        raise ValueError("service product evidence authentication failed")
    project_record = evidence.get("project_record")
    if not isinstance(project_record, Mapping) or project_record != {
        "last_event_sha256": execution.get("project_record", {}).get("last_event_sha256"),
        "event_count": execution.get("project_record", {}).get("event_count"),
    }:
        raise ValueError("service product Project Record evidence drifted")
    sources = result.get("officialSources", [])
    if evidence.get("official_sources") != sources:
        raise ValueError("service product official-source evidence drifted")
    materials = evidence.get("materials")
    if not isinstance(materials, list) or any(not isinstance(row, Mapping) for row in materials):
        raise ValueError("service product material evidence is invalid")
    return {**evidence, "authentication": deepcopy(dict(authentication))}


__all__ = [
    "authentication_message",
    "build_service_idempotency_replay_evidence",
    "build_service_product_evidence",
    "replay_authentication_message",
    "validate_service_idempotency_replay_evidence",
    "validate_service_product_evidence",
]
