"""Deterministic admission controls for versioned model-provider slots."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Mapping, Optional


CATALOG_CONTRACT = "lca_research_kernel.model_provider_catalog/v1"
RECEIPT_CONTRACT = "lca_research_kernel.model_provider_probe_receipt/v1"
REVIEW_CONTRACT = "lca_research_kernel.model_provider_admission_review/v1"
PROFILE_RECEIPT_CONTRACT = (
    "lca_research_kernel.model_schema_profile_probe_receipt/v1"
)
PROFILE_REVIEW_CONTRACT = (
    "lca_research_kernel.model_schema_profile_admission_review/v1"
)


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _self_hash(value: Mapping[str, object], field: str) -> bool:
    expected = value.get(field)
    payload = {key: deepcopy(item) for key, item in value.items() if key != field}
    return isinstance(expected, str) and expected == canonical_sha256(payload)


def _validate_provider_evidence(
    *,
    provider_id: str,
    receipt: Optional[Mapping[str, object]],
    independent_review: Optional[Mapping[str, object]],
    expected_catalog_sha256: str,
) -> None:
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("contract") != RECEIPT_CONTRACT
        or receipt.get("status") != "passed"
        or receipt.get("provider") != provider_id
        or receipt.get("provider_catalog_sha256") != expected_catalog_sha256
        or receipt.get("real_call") is not True
        or receipt.get("structured_output_valid") is not True
        or receipt.get("external_writes") != 0
        or receipt.get("scientific_result_created") is not False
        or not _self_hash(receipt, "receipt_sha256")
    ):
        raise ValueError("provider probe receipt is not admission-grade")
    if (
        not isinstance(independent_review, Mapping)
        or independent_review.get("contract") != REVIEW_CONTRACT
        or independent_review.get("status") != "passed"
        or independent_review.get("provider") != provider_id
        or independent_review.get("provider_catalog_sha256")
        != expected_catalog_sha256
        or independent_review.get("probe_receipt_sha256")
        != receipt["receipt_sha256"]
        or independent_review.get("p1") != 0
        or independent_review.get("p2") != 0
        or independent_review.get("external_effects") != 0
        or not _self_hash(independent_review, "review_sha256")
    ):
        raise ValueError("provider independent review is not admission-grade")


def admit_provider(
    catalog: Mapping[str, object],
    *,
    provider_id: str,
    receipt: Optional[Mapping[str, object]],
    independent_review: Optional[Mapping[str, object]],
) -> dict:
    """Promote one exact adapter slot only after probe and independent review."""

    if (
        catalog.get("contract") != CATALOG_CONTRACT
        or not isinstance(catalog.get("providers"), list)
        or not isinstance(provider_id, str)
        or not provider_id
    ):
        raise ValueError("provider catalog or identity is invalid")
    catalog_sha256 = canonical_sha256(catalog)
    matching_source = [
        row
        for row in catalog["providers"]
        if isinstance(row, Mapping) and row.get("provider_id") == provider_id
    ]
    if len(matching_source) != 1:
        raise ValueError("provider slot is absent or duplicated")
    source_row = matching_source[0]
    admitted_evidence = source_row.get("active_verified_evidence")
    expected_catalog_sha256 = catalog_sha256
    if source_row.get("admission_state") == "active_verified":
        if (
            not isinstance(admitted_evidence, list)
            or len(admitted_evidence) != 1
            or not isinstance(admitted_evidence[0], Mapping)
            or not isinstance(
                admitted_evidence[0].get("admitted_from_catalog_sha256"), str
            )
        ):
            raise ValueError("active provider admission evidence is invalid")
        expected_catalog_sha256 = admitted_evidence[0][
            "admitted_from_catalog_sha256"
        ]
    _validate_provider_evidence(
        provider_id=provider_id,
        receipt=receipt,
        independent_review=independent_review,
        expected_catalog_sha256=expected_catalog_sha256,
    )
    assert isinstance(receipt, Mapping)
    assert isinstance(independent_review, Mapping)
    promoted = deepcopy(dict(catalog))
    matching = [
        row
        for row in promoted["providers"]
        if isinstance(row, dict) and row.get("provider_id") == provider_id
    ]
    if len(matching) != 1:
        raise ValueError("provider slot is absent or duplicated")
    row = matching[0]
    if row.get("admission_state") == "active_verified":
        evidence = row.get("active_verified_evidence")
        expected = {
            "probe_receipt_sha256": receipt["receipt_sha256"],
            "independent_review_sha256": independent_review["review_sha256"],
            "admitted_from_catalog_sha256": expected_catalog_sha256,
        }
        if evidence == [expected]:
            return promoted
        raise ValueError("active provider evidence differs from requested admission")
    if (
        row.get("admission_state") != "adapter_ready_not_verified"
        or row.get("probe_eligible") is not True
    ):
        raise ValueError("provider slot is not eligible for admission")
    row["admission_state"] = "active_verified"
    row["active_verified_evidence"] = [
        {
            "probe_receipt_sha256": receipt["receipt_sha256"],
            "independent_review_sha256": independent_review["review_sha256"],
            "admitted_from_catalog_sha256": expected_catalog_sha256,
        }
    ]
    return promoted


def admit_providers(
    catalog: Mapping[str, object],
    admissions: Mapping[str, Mapping[str, object]],
) -> dict:
    """Atomically promote several providers against one frozen catalog identity."""

    if not isinstance(admissions, Mapping) or not admissions:
        raise ValueError("provider admissions are absent")
    promoted = deepcopy(dict(catalog))
    for provider_id, evidence in admissions.items():
        if not isinstance(evidence, Mapping):
            raise ValueError("provider admission evidence is invalid")
        single = admit_provider(
            catalog,
            provider_id=provider_id,
            receipt=evidence.get("receipt"),  # type: ignore[arg-type]
            independent_review=evidence.get("independent_review"),  # type: ignore[arg-type]
        )
        accepted_row = next(
            row
            for row in single["providers"]
            if row["provider_id"] == provider_id
        )
        target_index = next(
            index
            for index, row in enumerate(promoted["providers"])
            if row["provider_id"] == provider_id
        )
        promoted["providers"][target_index] = accepted_row
    return promoted


def _artifact_pointer(value: object, *, label: str) -> dict:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"path", "sha256"}
        or not isinstance(value.get("path"), str)
        or not value["path"]
        or not isinstance(value.get("sha256"), str)
        or len(value["sha256"]) != 64
    ):
        raise ValueError(f"{label} pointer is invalid")
    return deepcopy(dict(value))


def promote_provider_profile(
    catalog: Mapping[str, object],
    *,
    provider_id: str,
    profile_id: str,
    probe_pointer: Mapping[str, str],
    review_pointer: Mapping[str, str],
    provider_receipt: Optional[Mapping[str, object]],
    provider_review: Optional[Mapping[str, object]],
    profile_receipt: Optional[Mapping[str, object]],
    profile_review: Optional[Mapping[str, object]],
) -> dict:
    """Atomically admit one provider and one exact structured-output profile."""

    probe_ref = _artifact_pointer(
        probe_pointer, label="schema-profile probe"
    )
    review_ref = _artifact_pointer(
        review_pointer, label="schema-profile review"
    )
    if (
        not isinstance(profile_id, str)
        or not profile_id
        or not isinstance(profile_receipt, Mapping)
        or profile_receipt.get("contract") != PROFILE_RECEIPT_CONTRACT
        or profile_receipt.get("status") != "accepted"
        or profile_receipt.get("provider_id") != provider_id
        or profile_receipt.get("profile_id") != profile_id
        or profile_receipt.get("structured_output_valid") is not True
        or profile_receipt.get("tool_requests") != 0
        or profile_receipt.get("external_writes") != 0
        or profile_receipt.get("scientific_result_created") is not False
        or profile_receipt.get("sealed_reference_reads") != 0
        or not _self_hash(profile_receipt, "receipt_sha256")
    ):
        raise ValueError("schema-profile probe receipt is not admission-grade")
    if (
        not isinstance(profile_review, Mapping)
        or profile_review.get("contract") != PROFILE_REVIEW_CONTRACT
        or profile_review.get("status") != "passed"
        or profile_review.get("provider_id") != provider_id
        or profile_review.get("profile_id") != profile_id
        or profile_review.get("schema_sha256")
        != profile_receipt.get("schema_sha256")
        or profile_review.get("probe_receipt_sha256")
        != profile_receipt.get("receipt_sha256")
        or profile_review.get("p1") != 0
        or profile_review.get("p2") != 0
        or profile_review.get("external_effects") != 0
        or not _self_hash(profile_review, "review_sha256")
    ):
        raise ValueError("schema-profile review is not admission-grade")
    source_rows = [
        row
        for row in catalog.get("providers", [])
        if isinstance(row, Mapping) and row.get("provider_id") == provider_id
    ]
    if len(source_rows) != 1:
        raise ValueError("provider slot is absent or duplicated")
    if source_rows[0].get("admission_state") == "active_verified":
        _validate_provider_evidence(
            provider_id=provider_id,
            receipt=provider_receipt,
            independent_review=provider_review,
            expected_catalog_sha256=canonical_sha256(catalog),
        )
        promoted = deepcopy(dict(catalog))
    else:
        promoted = admit_provider(
            catalog,
            provider_id=provider_id,
            receipt=provider_receipt,
            independent_review=provider_review,
        )
    profiles = promoted.get("schema_profiles")
    if (
        not isinstance(profiles, Mapping)
        or not isinstance(profiles.get(profile_id), Mapping)
        or profiles[profile_id].get("schema_sha256")
        != profile_receipt.get("schema_sha256")
    ):
        raise ValueError("schema-profile identity is not catalog-bound")
    matching = [
        row
        for row in promoted["providers"]
        if isinstance(row, dict) and row.get("provider_id") == provider_id
    ]
    if len(matching) != 1:
        raise ValueError("provider slot is absent or duplicated")
    row = matching[0]
    capabilities = row.get("capabilities")
    evidence = row.get("capability_verified_evidence")
    if not isinstance(capabilities, dict) or not isinstance(evidence, dict):
        raise ValueError("provider capability contract is invalid")
    expected = {
        "schema_sha256": profile_receipt["schema_sha256"],
        "probe_receipt": probe_ref,
        "independent_review": review_ref,
    }
    existing = evidence.get(profile_id)
    if capabilities.get(profile_id) is True:
        if existing == expected:
            return promoted
        raise ValueError("active schema-profile evidence differs")
    if capabilities.get(profile_id) is not False or existing is not None:
        if evidence:
            raise ValueError("inactive schema-profile evidence already exists")
    capabilities[profile_id] = True
    evidence[profile_id] = expected
    return promoted
