"""Typed identities for official-source locators, snapshots, and observations."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Optional


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


def _self_bound(value: Mapping[str, object], field: str) -> dict:
    result = deepcopy(dict(value))
    result[field] = _canonical_sha256(result)
    return result


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is absent")
    return value.strip()


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _verify_self_bound(value: object, field: str) -> bool:
    if not isinstance(value, Mapping):
        return False
    expected = value.get(field)
    unsigned = {key: item for key, item in value.items() if key != field}
    return isinstance(expected, str) and expected == _canonical_sha256(unsigned)


def build_official_source_observation(
    *,
    source_id: str,
    authority: str,
    declared_version: str,
    requested_url: str,
    final_url: str,
    parse_location: str,
    observed_at: str,
    body_sha256: str,
    body_bytes: int,
    media_type: str,
    http_status: int,
    etag: object,
    last_modified: object,
    cache_ref: Mapping[str, object],
) -> dict:
    """Build one point-in-time observation without conflating its three identities."""

    source_id = _text(source_id, "source id")
    authority = _text(authority, "source authority")
    declared_version = _text(declared_version, "declared source version")
    requested_url = _text(requested_url, "requested URL")
    final_url = _text(final_url, "final URL")
    if not requested_url.startswith("https://") or not final_url.startswith("https://"):
        raise ValueError("official source URL must use HTTPS")
    parse_location = _text(parse_location, "parse location")
    observed_at = _text(observed_at, "observation time")
    body_sha256 = _sha(body_sha256, "body sha256")
    if isinstance(body_bytes, bool) or not isinstance(body_bytes, int) or body_bytes < 0:
        raise ValueError("official source body size is invalid")
    media_type = _text(media_type, "media type")
    if (
        isinstance(http_status, bool)
        or not isinstance(http_status, int)
        or http_status < 100
        or http_status > 599
    ):
        raise ValueError("official source HTTP status is invalid")
    if not isinstance(cache_ref, Mapping):
        raise ValueError("official source cache ref is invalid")
    if cache_ref.get("sha256") != body_sha256 or cache_ref.get("bytes") != body_bytes:
        raise ValueError("official source cache ref drifted")
    cache_path = _text(cache_ref.get("path"), "cache path")

    locator = _self_bound(
        {
            "contract": "lca_research_kernel.official_source_locator_identity/v1",
            "source_id": source_id,
            "authority": authority,
            "declared_version": declared_version,
            "requested_url": requested_url,
            "final_url": final_url,
            "parse_location": parse_location,
        },
        "locator_sha256",
    )
    snapshot = _self_bound(
        {
            "contract": "lca_research_kernel.official_source_snapshot_identity/v1",
            "body_sha256": body_sha256,
            "body_bytes": body_bytes,
            "media_type": media_type,
            "content_path": cache_path,
        },
        "snapshot_sha256",
    )
    observation = _self_bound(
        {
            "contract": "lca_research_kernel.official_source_observation/v1",
            "observed_at": observed_at,
            "locator_sha256": locator["locator_sha256"],
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "http_status": http_status,
            "etag": etag if isinstance(etag, str) else None,
            "last_modified": (
                last_modified if isinstance(last_modified, str) else None
            ),
        },
        "observation_sha256",
    )
    return {
        "contract": "lca_research_kernel.official_source_identity_binding/v1",
        "locator": locator,
        "snapshot": snapshot,
        "observation": observation,
        "content_ref": {
            "path": cache_path,
            "sha256": body_sha256,
            "bytes": body_bytes,
        },
    }


def freeze_official_source_snapshot(
    observation_binding: Mapping[str, object],
    *,
    body_ref: Optional[Mapping[str, object]] = None,
) -> dict:
    """Freeze a typed source snapshot without copying a point-in-time observation."""

    if not isinstance(observation_binding, Mapping) or observation_binding.get(
        "contract"
    ) != "lca_research_kernel.official_source_identity_binding/v1":
        raise ValueError("official source observation binding is invalid")
    locator = observation_binding.get("locator")
    snapshot = observation_binding.get("snapshot")
    observation = observation_binding.get("observation")
    if not (
        _verify_self_bound(locator, "locator_sha256")
        and _verify_self_bound(snapshot, "snapshot_sha256")
        and _verify_self_bound(observation, "observation_sha256")
    ):
        raise ValueError("official source observation identity drifted")
    assert isinstance(locator, Mapping)
    assert isinstance(snapshot, Mapping)
    assert isinstance(observation, Mapping)
    if not isinstance(body_ref, Mapping):
        raise ValueError("frozen source body ref is required")
    frozen = {
        "contract": "lca_research_kernel.frozen_official_source_snapshot/v1",
        "locator_sha256": locator["locator_sha256"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "body_sha256": snapshot["body_sha256"],
        "source_observation_sha256": observation["observation_sha256"],
    }
    frozen["body_ref"] = {
        "path": _text(body_ref.get("path"), "frozen source body path"),
        "sha256": _sha(body_ref.get("sha256"), "frozen source body sha256"),
    }
    if frozen["body_ref"]["sha256"] != frozen["body_sha256"]:
        raise ValueError("frozen source body ref differs from snapshot")
    frozen["frozen_binding_sha256"] = _canonical_sha256(frozen)
    return frozen


def _binding_valid(observed: object) -> bool:
    if not isinstance(observed, Mapping) or observed.get("contract") != (
        "lca_research_kernel.official_source_identity_binding/v1"
    ):
        return False
    locator = observed.get("locator")
    snapshot = observed.get("snapshot")
    observation = observed.get("observation")
    content_ref = observed.get("content_ref")
    return (
        _verify_self_bound(locator, "locator_sha256")
        and _verify_self_bound(snapshot, "snapshot_sha256")
        and _verify_self_bound(observation, "observation_sha256")
        and isinstance(locator, Mapping)
        and isinstance(snapshot, Mapping)
        and isinstance(observation, Mapping)
        and isinstance(content_ref, Mapping)
        and locator.get("contract")
        == "lca_research_kernel.official_source_locator_identity/v1"
        and all(
            isinstance(locator.get(field), str) and bool(locator.get(field))
            for field in (
                "source_id",
                "authority",
                "declared_version",
                "requested_url",
                "final_url",
                "parse_location",
            )
        )
        and str(locator.get("requested_url")).startswith("https://")
        and str(locator.get("final_url")).startswith("https://")
        and snapshot.get("contract")
        == "lca_research_kernel.official_source_snapshot_identity/v1"
        and isinstance(snapshot.get("body_sha256"), str)
        and SHA256_RE.fullmatch(str(snapshot.get("body_sha256"))) is not None
        and isinstance(snapshot.get("body_bytes"), int)
        and not isinstance(snapshot.get("body_bytes"), bool)
        and snapshot.get("body_bytes") >= 0
        and isinstance(snapshot.get("media_type"), str)
        and bool(snapshot.get("media_type"))
        and isinstance(snapshot.get("content_path"), str)
        and bool(snapshot.get("content_path"))
        and observation.get("contract")
        == "lca_research_kernel.official_source_observation/v1"
        and isinstance(observation.get("observed_at"), str)
        and bool(observation.get("observed_at"))
        and isinstance(observation.get("http_status"), int)
        and not isinstance(observation.get("http_status"), bool)
        and 100 <= observation.get("http_status") <= 599
        and all(
            observation.get(field) is None
            or isinstance(observation.get(field), str)
            for field in ("etag", "last_modified")
        )
        and observation.get("locator_sha256") == locator.get("locator_sha256")
        and observation.get("snapshot_sha256") == snapshot.get("snapshot_sha256")
        and isinstance(content_ref.get("path"), str)
        and bool(content_ref.get("path"))
        and content_ref.get("path") == snapshot.get("content_path")
        and content_ref.get("sha256") == snapshot.get("body_sha256")
        and content_ref.get("bytes") == snapshot.get("body_bytes")
    )


def _content_ref_matches_bytes(
    observed_binding: Mapping[str, object], root: Optional[Path]
) -> bool:
    """Verify the observation's registered object under its declared root."""

    if root is None:
        return False
    content_ref = observed_binding.get("content_ref")
    snapshot = observed_binding.get("snapshot")
    if not isinstance(content_ref, Mapping) or not isinstance(snapshot, Mapping):
        return False
    try:
        relative = Path(_text(content_ref.get("path"), "source content path"))
        resolved_root = Path(root).resolve()
        path = (resolved_root / relative).resolve()
        path.relative_to(resolved_root)
        payload = path.read_bytes()
    except (ValueError, OSError):
        return False
    return (
        len(payload) == content_ref.get("bytes")
        and hashlib.sha256(payload).hexdigest() == content_ref.get("sha256")
        and content_ref.get("sha256") == snapshot.get("body_sha256")
    )


def validate_official_source_observation(observed: object) -> bool:
    """Return whether a typed observation is internally content-bound."""

    return _binding_valid(observed)


def classify_frozen_source_relation(
    frozen_source: Mapping[str, object],
    observed_binding: Mapping[str, object],
    *,
    root: Optional[Path] = None,
) -> dict:
    """Classify source evolution without mutating frozen evidence or its oracle."""

    if not isinstance(frozen_source, Mapping) or not _binding_valid(observed_binding):
        return _relation("unresolved_fail_closed", accepted=False, usable=False)
    locator = observed_binding["locator"]
    snapshot = observed_binding["snapshot"]
    assert isinstance(locator, Mapping)
    assert isinstance(snapshot, Mapping)

    if frozen_source.get("contract") != (
        "lca_research_kernel.frozen_official_source_snapshot/v1"
    ):
        legacy_url = frozen_source.get("url")
        legacy_digest = frozen_source.get("sha256")
        legacy_parse_location = frozen_source.get("parse_location")
        legacy_version = frozen_source.get("version")
        legacy_authority = frozen_source.get("identity")
        locator_matches = legacy_url in {
            locator.get("requested_url"),
            locator.get("final_url"),
        }
        if (
            isinstance(legacy_digest, str)
            and SHA256_RE.fullmatch(legacy_digest) is not None
            and locator_matches
            and _content_ref_matches_bytes(observed_binding, root)
            and (
                legacy_parse_location is None
                or legacy_parse_location == locator.get("parse_location")
            )
            and (
                legacy_version is None
                or legacy_version == locator.get("declared_version")
            )
            and (
                legacy_authority is None
                or legacy_authority == locator.get("authority")
            )
        ):
            return {
                **_relation(
                    "legacy_untyped_digest_recovered",
                    accepted=False,
                    usable=True,
                ),
                "legacy_digest": legacy_digest,
                "frozen_digest_is_raw_body_sha256": False,
                "observed_snapshot_sha256": snapshot["snapshot_sha256"],
                "observed_body_sha256": snapshot["body_sha256"],
            }
        return _relation("unresolved_fail_closed", accepted=False, usable=False)

    frozen_unsigned = {
        key: value
        for key, value in frozen_source.items()
        if key != "frozen_binding_sha256"
    }
    if frozen_source.get("frozen_binding_sha256") != _canonical_sha256(
        frozen_unsigned
    ):
        return _relation("unresolved_fail_closed", accepted=False, usable=False)
    body_ref = frozen_source.get("body_ref")
    if not isinstance(body_ref, Mapping) or root is None:
        return _relation("unresolved_fail_closed", accepted=False, usable=False)
    try:
        relative = Path(_text(body_ref.get("path"), "frozen body path"))
        path = (Path(root).resolve() / relative).resolve()
        path.relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return _relation("unresolved_fail_closed", accepted=False, usable=False)
    expected = body_ref.get("sha256")
    if (
        not path.is_file()
        or not isinstance(expected, str)
        or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        or expected != frozen_source.get("body_sha256")
    ):
        return _relation("unresolved_fail_closed", accepted=False, usable=False)

    if frozen_source.get("locator_sha256") != locator.get("locator_sha256"):
        return _relation("unresolved_fail_closed", accepted=False, usable=False)
    if frozen_source.get("snapshot_sha256") == snapshot.get("snapshot_sha256"):
        return {
            **_relation("exact_snapshot_observed", accepted=True, usable=True),
            "frozen_snapshot_sha256": frozen_source["snapshot_sha256"],
            "candidate_snapshot_sha256": snapshot["snapshot_sha256"],
        }
    return {
        **_relation(
            "same_locator_candidate_revision",
            accepted=False,
            usable=False,
        ),
        "frozen_snapshot_sha256": frozen_source.get("snapshot_sha256"),
        "candidate_snapshot_sha256": snapshot.get("snapshot_sha256"),
    }


def _relation(relation: str, *, accepted: bool, usable: bool) -> dict:
    return {
        "contract": "lca_research_kernel.official_source_relation/v1",
        "relation": relation,
        "accepted_for_frozen_evidence": accepted,
        "usable_for_bounded_evaluation": usable,
        "mutation_permitted": False,
        "next_route": {
            "exact_snapshot_observed": "continue-with-frozen-source",
            "same_locator_candidate_revision": "review-candidate-source-revision",
            "legacy_untyped_digest_recovered": "freeze-typed-source-before-next-evaluation",
            "unresolved_fail_closed": "repair-source-identity-binding",
        }[relation],
    }
