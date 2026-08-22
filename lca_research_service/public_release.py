"""Content-bound basis for a clean, default-deny public release."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from lca_research_kernel.lifecycle import validate_project_lifecycle_events
from lca_research_service.product_readiness_gate import canonical_sha256
from lca_research_service.release_manifest import (
    validate_runtime_release_manifest_evidence,
)


PUBLIC_RELEASE_CONTRACT = "public-release-manifest/v1"
_POINTER_CONTRACT = "d18.current_product_readiness_release/v1"
_RECEIPT_CONTRACT = "d18.product_readiness_release_receipt/v1"
_PUBLICATION_RECEIPT_CONTRACT = "d18.publication_receipt/v1"
_EXPECTED_JOURNEYS = {
    "direct_no_material_explanation",
    "medium_material_explanation",
    "official_source_lca_question",
    "professional_missing_data_recovery",
    "tiangong_native_bounded_contract",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unsigned_sha256(value: Mapping[str, Any], field: str) -> str:
    unsigned = deepcopy(dict(value))
    unsigned.pop(field, None)
    return canonical_sha256(unsigned)


def _manifest_sha256(value: Mapping[str, Any]) -> str:
    return _unsigned_sha256(value, "manifest_sha256")


def bind_public_tree_to_manifest(
    manifest: Mapping[str, object], public_tree_sha256: str
) -> dict[str, Any]:
    if _SHA256.fullmatch(public_tree_sha256) is None:
        raise ValueError("public tree hash is invalid")
    value = deepcopy(dict(manifest))
    value["public_tree_sha256"] = public_tree_sha256
    value["manifest_sha256"] = _manifest_sha256(value)
    return value


def _read_json_file(path: Path, unavailable: str, invalid: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(unavailable)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(invalid) from error
    if not isinstance(value, dict):
        raise ValueError(invalid)
    return value


def _confined_file(base: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("product readiness receipt reference is invalid")
    root = base.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError("product readiness receipt escaped its root") from error
    if not target.is_file() or target.is_symlink():
        raise ValueError("product readiness receipt is unavailable")
    return target


def _git(root: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("accepted release Git basis is unavailable") from error


def _git_blob(root: Path, commit: str, relative: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "show", f"{commit}:{relative}"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("accepted D-18 contract is unavailable") from error


def _load_accepted_runtime_manifest(
    receipt_path: Path, receipt: Mapping[str, Any]
) -> dict[str, Any]:
    embedded = receipt.get("runtime_release_manifest")
    if embedded is not None:
        return validate_runtime_release_manifest_evidence(embedded)

    # Historical receipts predate the self-contained release basis and remain
    # replayable inside the private evidence archive. New accepted receipts do
    # not require raw journey observations to construct a clean release.
    manifests: list[dict[str, Any]] = []
    release_root = receipt_path.parent
    for row in receipt["journeys"]:
        journey = row["journey"]
        observation_path = release_root / "journeys" / journey / "observation.json"
        observation = _read_json_file(
            observation_path,
            "product readiness observation is unavailable",
            "product readiness observation is invalid",
        )
        if (
            _file_sha256(observation_path) != row.get("observation_file_sha256")
            or canonical_sha256(observation) != row.get("observation_sha256")
            or observation.get("journey") != journey
        ):
            raise ValueError("product readiness observation drifted")
        result_view = observation.get("result_view")
        product_evidence = (
            result_view.get("productEvidence")
            if isinstance(result_view, Mapping)
            else None
        )
        runtime_manifest = (
            product_evidence.get("release_manifest")
            if isinstance(product_evidence, Mapping)
            else None
        )
        manifests.append(validate_runtime_release_manifest_evidence(runtime_manifest))
    if not manifests or any(item != manifests[0] for item in manifests[1:]):
        raise ValueError("product readiness runtime release identities diverged")
    return manifests[0]


def _load_readiness_basis(pointer_path: Path) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    pointer_path = Path(pointer_path).resolve()
    pointer = _read_json_file(
        pointer_path,
        "product readiness pointer is unavailable",
        "product readiness pointer is invalid",
    )
    if (
        pointer.get("contract") != _POINTER_CONTRACT
        or pointer.get("pointer_sha256") != _unsigned_sha256(pointer, "pointer_sha256")
    ):
        raise ValueError("product readiness pointer drifted")
    receipt_path = _confined_file(pointer_path.parent, pointer.get("receipt_ref"))
    receipt = _read_json_file(
        receipt_path,
        "product readiness receipt is unavailable",
        "product readiness receipt is invalid",
    )
    journeys = receipt.get("journeys")
    journey_names = {
        row.get("journey") for row in journeys if isinstance(row, Mapping)
    } if isinstance(journeys, list) else set()
    journey_count_is_invalid = not isinstance(journeys, list) or len(journeys) != 5
    if (
        receipt.get("contract") != _RECEIPT_CONTRACT
        or receipt.get("status") != "passed"
        or receipt.get("errors") != []
        or journey_count_is_invalid
        or journey_names != _EXPECTED_JOURNEYS
    ):
        raise ValueError("product readiness release requires five passed journeys")
    if (
        receipt.get("receipt_sha256") != _unsigned_sha256(receipt, "receipt_sha256")
        or pointer.get("receipt_sha256") != receipt.get("receipt_sha256")
        or pointer.get("receipt_file_sha256") != _file_sha256(receipt_path)
        or pointer.get("release_id") != receipt.get("release_id")
        or pointer.get("journey_count") != 5
    ):
        raise ValueError("product readiness release evidence drifted")
    return pointer, receipt_path, receipt


def build_public_release_manifest(
    repository_root: Path, readiness_pointer: Path
) -> dict[str, object]:
    """Freeze the accepted private basis; the public tree is populated after export."""

    root = Path(repository_root).resolve()
    pointer, receipt_path, receipt = _load_readiness_basis(readiness_pointer)
    runtime = _load_accepted_runtime_manifest(receipt_path, receipt)
    source_commit = str(runtime["backend"]["git_commit"])
    source_tree_oid = _git(root, "rev-parse", f"{source_commit}^{{tree}}")
    object_format = _git(root, "rev-parse", "--show-object-format")
    if _GIT_OID.fullmatch(source_tree_oid) is None or object_format not in {
        "sha1",
        "sha256",
    }:
        raise ValueError("accepted release Git basis is invalid")
    architecture_sha256 = hashlib.sha256(
        _git_blob(root, source_commit, "docs/lca-agent-top-level-architecture.json")
    ).hexdigest()
    manifest: dict[str, Any] = {
        "contract": PUBLIC_RELEASE_CONTRACT,
        "accepted_product_readiness": {
            "release_id": pointer["release_id"],
            "receipt_sha256": pointer["receipt_sha256"],
        },
        "private_archive": {
            "source_commit": source_commit,
            "source_tree_oid": source_tree_oid,
            "git_object_format": object_format,
        },
        "d18_contracts": {
            "top_level_architecture_sha256": architecture_sha256,
        },
        "build": {
            "runtime_release_id": runtime["release_id"],
            "backend_tree_sha256": runtime["backend"]["source_tree_sha256"],
            "frontend_input_sha256": runtime["frontend"]["input_sha256"],
        },
        "provider": {
            "catalog_sha256": runtime["provider"]["provider_catalog_sha256"],
            "profile_policy_sha256": runtime["provider"]["policy_sha256"],
        },
        "public_tree_sha256": "",
    }
    manifest["manifest_sha256"] = _manifest_sha256(manifest)
    return manifest


def _read_lifecycle_events(repository_root: Path) -> list[dict[str, Any]]:
    event_path = (
        repository_root
        / "outputs/council-default-lifecycle/research-events.jsonl"
    )
    if not event_path.is_file() or event_path.is_symlink():
        raise ValueError("accepted lifecycle history is unavailable")
    events: list[dict[str, Any]] = []
    try:
        for raw_line in event_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            event = json.loads(raw_line)
            if not isinstance(event, dict):
                raise ValueError
            events.append(event)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("accepted lifecycle history is invalid") from error
    return events


def _historical_readiness_pointer(
    repository_root: Path, binding: Mapping[str, Any]
) -> Path:
    release_id = binding.get("release_id")
    receipt_sha256 = binding.get("receipt_sha256")
    if (
        not isinstance(release_id, str)
        or _SHA256.fullmatch(release_id) is None
        or not isinstance(receipt_sha256, str)
        or _SHA256.fullmatch(receipt_sha256) is None
    ):
        raise ValueError("accepted Product Readiness binding is invalid")
    matches: set[Path] = set()
    for event in _read_lifecycle_events(repository_root):
        if event.get("event_type") != "research.lifecycle.beat_accepted":
            continue
        payload = event.get("payload")
        artifact = (
            payload.get("accepted_artifact")
            if isinstance(payload, Mapping)
            else None
        )
        relative = artifact.get("path") if isinstance(artifact, Mapping) else None
        if not isinstance(relative, str):
            continue
        parts = Path(relative).parts
        if (
            len(parts) < 4
            or parts[-4:] != (
                "releases",
                release_id,
                receipt_sha256,
                "product-readiness-release.json",
            )
        ):
            continue
        artifact_path = (repository_root / relative).resolve()
        try:
            artifact_path.relative_to(repository_root)
        except ValueError as error:
            raise ValueError("accepted lifecycle artifact escaped repository") from error
        if (
            not artifact_path.is_file()
            or artifact_path.is_symlink()
            or artifact.get("sha256") != _file_sha256(artifact_path)
        ):
            raise ValueError("accepted Product Readiness history drifted")
        pointer_path = artifact_path.parents[3] / "current-release.json"
        pointer, _, _ = _load_readiness_basis(pointer_path)
        if (
            pointer.get("release_id") != release_id
            or pointer.get("receipt_sha256") != receipt_sha256
        ):
            raise ValueError("accepted Product Readiness history drifted")
        matches.add(pointer_path)
    if len(matches) != 1:
        raise ValueError("accepted Product Readiness history is not unique")
    return matches.pop()


def _authoritative_pointer(
    repository_root: Path, binding: Mapping[str, Any]
) -> Path:
    state_path = repository_root / "outputs/council-default-lifecycle/lifecycle-state.json"
    state = _read_json_file(
        state_path,
        "accepted lifecycle state is unavailable",
        "accepted lifecycle state is invalid",
    )
    artifact = state.get("accepted_artifact")
    relative = artifact.get("path") if isinstance(artifact, Mapping) else None
    if not isinstance(relative, str) or not relative:
        raise ValueError("accepted lifecycle artifact is invalid")
    artifact_path = (repository_root / relative).resolve()
    try:
        artifact_path.relative_to(repository_root)
    except ValueError as error:
        raise ValueError("accepted lifecycle artifact escaped repository") from error
    if (
        not artifact_path.is_file()
        or artifact_path.is_symlink()
        or artifact.get("sha256") != _file_sha256(artifact_path)
    ):
        raise ValueError("accepted lifecycle artifact drifted")
    events = _read_lifecycle_events(repository_root)
    try:
        projection = validate_project_lifecycle_events(events)
    except ValueError as error:
        raise ValueError(f"accepted lifecycle history is invalid: {error}") from error
    if state != projection:
        raise ValueError("accepted lifecycle state drifted")
    if artifact_path.name == "product-readiness-release.json":
        return artifact_path.parents[3] / "current-release.json"
    publication_paths: set[Path] = set()
    for event in events:
        if event.get("event_type") != "research.lifecycle.beat_accepted":
            continue
        payload = event.get("payload")
        historical_artifact = (
            payload.get("accepted_artifact")
            if isinstance(payload, Mapping)
            else None
        )
        relative = (
            historical_artifact.get("path")
            if isinstance(historical_artifact, Mapping)
            else None
        )
        if not isinstance(relative, str):
            continue
        candidate = (repository_root / relative).resolve()
        try:
            candidate.relative_to(repository_root)
        except ValueError as error:
            raise ValueError("accepted lifecycle artifact escaped repository") from error
        if candidate.name != "publication-receipt-v2.json":
            continue
        if (
            not candidate.is_file()
            or candidate.is_symlink()
            or historical_artifact.get("sha256") != _file_sha256(candidate)
        ):
            raise ValueError("accepted publication history drifted")
        publication = _read_json_file(
            candidate,
            "accepted publication receipt is unavailable",
            "accepted publication receipt is invalid",
        )
        basis = publication.get("release_basis")
        if (
            publication.get("contract") == _PUBLICATION_RECEIPT_CONTRACT
            and publication.get("status") == "passed"
            and publication.get("receipt_sha256")
            == _unsigned_sha256(publication, "receipt_sha256")
            and isinstance(basis, Mapping)
            and basis.get("release_id") == binding.get("release_id")
            and basis.get("accepted_product_readiness_receipt_sha256")
            == binding.get("receipt_sha256")
        ):
            publication_paths.add(candidate)
    if len(publication_paths) != 1:
        raise ValueError("accepted publication basis drifted")
    return _historical_readiness_pointer(repository_root, binding)


def validate_public_release_manifest(
    manifest: Mapping[str, object],
    repository_root: Path,
    *,
    require_public_tree: bool = False,
) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise ValueError("public release manifest is invalid")
    value = deepcopy(dict(manifest))
    if value.get("contract") != PUBLIC_RELEASE_CONTRACT:
        raise ValueError("public release manifest contract is invalid")
    if value.get("manifest_sha256") != _manifest_sha256(value):
        raise ValueError("public release manifest hash drifted")
    public_tree_sha256 = value.get("public_tree_sha256")
    if require_public_tree and not public_tree_sha256:
        raise ValueError("public tree hash is absent")
    if public_tree_sha256 and _SHA256.fullmatch(str(public_tree_sha256)) is None:
        raise ValueError("public tree hash is invalid")
    root = Path(repository_root).resolve()
    binding = value.get("accepted_product_readiness")
    if not isinstance(binding, Mapping):
        raise ValueError("accepted Product Readiness binding is invalid")
    expected = build_public_release_manifest(
        root, _authoritative_pointer(root, binding)
    )
    compared = deepcopy(value)
    compared["public_tree_sha256"] = ""
    compared["manifest_sha256"] = expected["manifest_sha256"]
    if compared != expected:
        raise ValueError("accepted release basis drifted")
    return value
