"""Classify bounded exact-author public profiles and hosted files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence


JsonObject = Dict[str, object]

AUTHOR_HOSTED_CONTRACT = (
    "lca_research_kernel.author_hosted_public_file_routes/v1"
)
AUTHOR_INSTITUTION_CONTRACT = (
    "lca_research_kernel.author_institution_public_routes/v1"
)
KOREAN_SOURCE_CONTRACT = (
    "lca_research_kernel.korean_scholarly_source_routes/v1"
)

JAEHOON_ORCID = "0000-0001-6188-7571"
KYUNG_YOON_ORCID = "0000-0002-1273-746X"
BYUNG_WON_ORCID = "0000-0003-0266-8672"

REVIEWED_ROUTE_TEMPLATES = (
    {
        "route_id": "skku-pure-jaehoon-profile",
        "source_owner": "Sungkyunkwan University",
        "source_class": "official_university_researcher_profile",
        "request_url": "https://pure.skku.edu/en/persons/jaehoon-kim/",
        "reviewed_hosts": ["pure.skku.edu"],
        "response_kind": "html",
        "expected_document_role": "author_profile_html",
        "author_orcids": [JAEHOON_ORCID],
        "required_text_occurrences": ["Jaehoon Kim", "gemp.skku.edu"],
        "reviewed_target_occurrence_dois": [],
        "expected_sha256": None,
        "discovery_basis": "exact_skku_pure_identity_profile",
        "access_basis": "public_official_university_profile",
    },
    {
        "route_id": "skku-gemp-lab-home",
        "source_owner": "Sungkyunkwan University GEMP Laboratory",
        "source_class": "official_author_laboratory_site",
        "request_url": "https://gemp.skku.edu/",
        "reviewed_hosts": ["gemp.skku.edu"],
        "response_kind": "html",
        "expected_document_role": "author_lab_html",
        "author_orcids": [JAEHOON_ORCID],
        "required_text_occurrences": ["gemp3.blogspot.com"],
        "reviewed_target_occurrence_dois": [],
        "expected_sha256": None,
        "discovery_basis": "official_site_link_from_exact_skku_pure_profile",
        "access_basis": "public_official_university_lab_site",
    },
    {
        "route_id": "skku-gemp-jaehoon-cv",
        "source_owner": "Sungkyunkwan University GEMP Laboratory",
        "source_class": "official_author_laboratory_file",
        "request_url": (
            "https://gemp.skku.edu/images/"
            "Jaehoon%20Kim%20CV_2013.11.25.pdf"
        ),
        "reviewed_hosts": ["gemp.skku.edu"],
        "response_kind": "pdf",
        "expected_document_role": "author_cv",
        "author_orcids": [JAEHOON_ORCID],
        "required_text_occurrences": [],
        "reviewed_target_occurrence_dois": [
            "10.1016/j.supflu.2010.09.026",
            "10.1016/j.cej.2012.05.058",
        ],
        "expected_sha256": (
            "2220154b7af462378f85dec99b05a6537dc577144795e2e91f1cb08c5b7a916c"
        ),
        "discovery_basis": (
            "exact_skku_profile_to_official_lab_domain_plus_reviewed_public_file"
        ),
        "access_basis": "public_official_university_lab_file",
    },
    {
        "route_id": "kistoday-2009-battery-researcher-feature",
        "source_owner": "Korea Institute of Science and Technology",
        "source_class": "official_institutional_profile_publication",
        "request_url": (
            "https://www.kist.re.kr/_attach/kist/old/186/"
            "2021120114440147_0.pdf"
        ),
        "reviewed_hosts": ["www.kist.re.kr", "kist.re.kr"],
        "response_kind": "pdf",
        "expected_document_role": "institutional_profile_publication",
        "author_orcids": [KYUNG_YOON_ORCID, BYUNG_WON_ORCID],
        "required_text_occurrences": [],
        "reviewed_target_occurrence_dois": [],
        "expected_sha256": (
            "18e0477cf6a4001503ff6984d2ffd61636acb399bdb128b59e35dffcfe84a588"
        ),
        "discovery_basis": "official_kist_publication_exact_author_feature",
        "access_basis": "public_official_institutional_publication",
    },
)

NON_TARGET_DOCUMENT_ROLES = {
    "author_profile_html",
    "author_lab_html",
    "author_cv",
    "institutional_profile_publication",
    "presentation",
    "thesis",
    "citing_paper",
}


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _artifact(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an artifact binding")
    path = _required_text(value.get("path"), f"{label}.path")
    sha256 = _required_text(value.get("sha256"), f"{label}.sha256")
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise ValueError(f"{label}.sha256 must be a lowercase sha256")
    return {"path": path, "sha256": sha256}


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _normalize_doi(value: object) -> str:
    text = _required_text(value, "DOI").casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.strip()


def _normalize_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", _required_text(value, "title"))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"(?<=[A-Za-z])\((\d+)\)", r"\1", text)
    return " ".join(re.findall(r"[\w]+", text.casefold()))


def _canonical_url(value: object) -> str:
    text = _required_text(value, "URL")
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be public HTTP(S)")
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path or "/",
            parsed.query,
            "",
        )
    )


def _accepted_upstream(report: JsonObject, contract: str) -> bool:
    return (
        report.get("contract") == contract
        and report.get("scientific_field_evidence_admitted") is False
        and report.get("scientific_comparison_ready") is False
        and report.get("comparative_lcia_allowed") is False
        and report.get("routing", {}).get("pause_internal_work") is False
    )


def _current_targets(korean_report: JsonObject) -> List[JsonObject]:
    records = korean_report.get("record_results")
    if not isinstance(records, list) or len(records) != 2:
        raise ValueError("exactly two current metadata-only target records are required")
    targets = []
    seen = set()
    for record in records:
        if not isinstance(record, dict) or record.get("status") != "exact_cris_record_metadata_only":
            raise ValueError("author-hosted routes require metadata-only exact CRIS records")
        target_id = _required_text(record.get("target_id"), "target_id")
        if target_id in seen:
            raise ValueError(f"duplicate target_id: {target_id}")
        seen.add(target_id)
        targets.append(
            {
                "target_id": target_id,
                "doi": _normalize_doi(record.get("doi")),
                "title": _required_text(record.get("title"), f"{target_id}.title"),
            }
        )
    return sorted(targets, key=lambda row: row["target_id"])


def _accepted_authors(
    author_report: JsonObject, targets: List[JsonObject]
) -> List[JsonObject]:
    rows = author_report.get("author_results")
    if not isinstance(rows, list) or len(rows) != 3:
        raise ValueError("exactly three accepted author identities are required")
    target_by_id = {row["target_id"]: row for row in targets}
    accepted = []
    seen = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("identity_confidence") != "exact_name_match"
            or row.get("works_status") != "target_work_matches"
        ):
            raise ValueError("accepted exact author identities are required")
        orcid = _required_text(row.get("orcid"), "orcid")
        if orcid in seen:
            raise ValueError(f"duplicate author ORCID: {orcid}")
        seen.add(orcid)
        target_ids = row.get("target_ids")
        matches = row.get("target_work_matches")
        if not isinstance(target_ids, list) or not isinstance(matches, list):
            raise ValueError("author target identity bindings are malformed")
        match_by_id = {
            match.get("target_id"): match
            for match in matches
            if isinstance(match, dict)
        }
        if set(target_ids) != set(match_by_id):
            raise ValueError("author target identity bindings are incomplete")
        for target_id in target_ids:
            target = target_by_id.get(target_id)
            match = match_by_id.get(target_id)
            if (
                target is None
                or not isinstance(match, dict)
                or _normalize_doi(match.get("doi")) != target["doi"]
                or _normalize_title(match.get("title"))
                != _normalize_title(target["title"])
            ):
                raise ValueError("author target identity conflicts with current records")
        accepted.append(
            {
                "orcid": orcid,
                "public_name": _required_text(row.get("public_name"), "public_name"),
                "target_ids": sorted(target_ids),
            }
        )
    required = {JAEHOON_ORCID, KYUNG_YOON_ORCID, BYUNG_WON_ORCID}
    if seen != required:
        raise ValueError("accepted vertical author identities changed")
    return sorted(accepted, key=lambda row: row["orcid"])


def compile_author_hosted_manifest(
    author_report: JsonObject,
    author_binding: JsonObject,
    korean_report: JsonObject,
    korean_binding: JsonObject,
) -> JsonObject:
    """Compile reviewed exact-author public profile and file routes."""

    if not _accepted_upstream(author_report, AUTHOR_INSTITUTION_CONTRACT):
        raise ValueError("accepted author/institution route is required")
    if (
        not _accepted_upstream(korean_report, KOREAN_SOURCE_CONTRACT)
        or korean_report.get("routing", {}).get("next_internal_route")
        != "expand-author-hosted-public-files"
        or korean_report.get("lawful_attachment_candidate_count") != 0
        or korean_report.get("scientific_absence_proven") is not False
    ):
        raise ValueError("accepted Korean-source route is required")
    if (
        author_report.get("scope_id") != korean_report.get("scope_id")
        or author_report.get("revision") != korean_report.get("revision")
    ):
        raise ValueError("author and Korean-source scope identity conflicts")
    targets = _current_targets(korean_report)
    authors = _accepted_authors(author_report, targets)
    author_by_orcid = {row["orcid"]: row for row in authors}
    target_by_doi = {row["doi"]: row for row in targets}
    requests = []
    registry = []
    for template in REVIEWED_ROUTE_TEMPLATES:
        author_orcids = list(template["author_orcids"])
        target_ids = sorted(
            {
                target_id
                for orcid in author_orcids
                for target_id in author_by_orcid[orcid]["target_ids"]
            }
        )
        occurrences = []
        for doi in template["reviewed_target_occurrence_dois"]:
            target = target_by_doi.get(doi)
            if target is None or target["target_id"] not in target_ids:
                raise ValueError("reviewed target occurrence left its author binding")
            occurrences.append(
                {
                    "target_id": target["target_id"],
                    "doi": target["doi"],
                    "title": target["title"],
                    "occurrence_semantics": "citation_or_listing_only",
                    "review_basis": "independent_pdf_text_and_visual_inspection",
                }
            )
        row = {
            **template,
            "request_url": _canonical_url(template["request_url"]),
            "author_orcids": author_orcids,
            "author_names": [author_by_orcid[value]["public_name"] for value in author_orcids],
            "target_ids": target_ids,
            "reviewed_target_occurrences": sorted(
                occurrences, key=lambda value: value["target_id"]
            ),
            "route_status": "reviewed_public_route",
            "execution_policy": "bounded_public_read_no_contact",
        }
        row.pop("reviewed_target_occurrence_dois", None)
        registry.append(row)
        requests.append({**row, "request_id": row["route_id"]})
    registry.sort(key=lambda row: row["route_id"])
    requests.sort(key=lambda row: row["request_id"])
    retained = korean_report.get("routing", {}).get("retained_retry_requests", [])
    if not isinstance(retained, list) or not all(isinstance(row, dict) for row in retained):
        raise ValueError("retained retry requests are malformed")
    credential_routes = korean_report.get("routing", {}).get(
        "credential_required_routes", []
    )
    if not isinstance(credential_routes, list):
        raise ValueError("credential-required routes are malformed")
    return {
        "schema_version": 1,
        "contract": AUTHOR_HOSTED_CONTRACT,
        "scope_id": _required_text(korean_report.get("scope_id"), "scope_id"),
        "revision": _required_text(korean_report.get("revision"), "revision"),
        "status": "author_hosted_public_file_routes_compiled",
        "author_institution_report": _artifact(author_binding, "author_binding"),
        "korean_source_report": _artifact(korean_binding, "korean_binding"),
        "author_count": len(authors),
        "target_count": len(targets),
        "route_count": len(registry),
        "authors": authors,
        "targets": targets,
        "route_registry": registry,
        "public_requests": requests,
        "credential_required_route_count": korean_report.get(
            "credential_required_route_count", 0
        ),
        "scope_inapplicable_route_count": korean_report.get(
            "scope_inapplicable_route_count", 0
        ),
        "credential_required_routes": credential_routes,
        "retained_retry_requests": retained,
        "routing": {
            "human_routes_untouched": korean_report.get("routing", {}).get(
                "human_routes_untouched", []
            ),
            "next_internal_route": "execute-author-hosted-public-routes",
            "pause_internal_work": False,
        },
        "scientific_field_evidence_admitted": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
    }


def _safe_name(request_id: str) -> str:
    return urllib.parse.quote(request_id, safe="")


def _envelope_path(request_id: str) -> str:
    return f"envelopes/{_safe_name(request_id)}.json"


def _binary_path(request_id: str) -> str:
    return f"files/{_safe_name(request_id)}.pdf"


def expected_author_hosted_output_paths(manifest: JsonObject) -> List[str]:
    requests = manifest.get("public_requests")
    if not isinstance(requests, list):
        raise ValueError("public requests are malformed")
    paths = []
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError("public request is malformed")
        request_id = _required_text(request.get("request_id"), "request_id")
        paths.append(_envelope_path(request_id))
        if request.get("response_kind") == "pdf":
            paths.append(_binary_path(request_id))
    return sorted(paths)


def classify_public_response(
    request: JsonObject,
    body: bytes,
    *,
    content_type: str,
    response_url: str,
    response_sha256: str,
) -> JsonObject:
    """Classify media and role before any target-identity occurrence."""

    if not isinstance(body, bytes):
        raise ValueError("public response body must be bytes")
    actual_url = _canonical_url(response_url)
    host = urllib.parse.urlsplit(actual_url).hostname
    reviewed_hosts = request.get("reviewed_hosts")
    if not isinstance(reviewed_hosts, list) or host not in reviewed_hosts:
        raise ValueError("public response left the reviewed host allowlist")
    sha256 = _required_text(response_sha256, "response_sha256")
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise ValueError("response_sha256 must be a lowercase sha256")
    response_kind = request.get("response_kind")
    media = str(content_type or "").split(";", 1)[0].strip().casefold()
    if response_kind == "pdf":
        if not body.startswith(b"%PDF-"):
            raise ValueError("PDF magic bytes are missing")
        if media not in {"application/pdf", "application/octet-stream"}:
            raise ValueError("PDF response media type conflicts with the route")
        expected_hash = _required_text(request.get("expected_sha256"), "expected_sha256")
        if sha256 != expected_hash:
            raise ValueError("public PDF changed from its reviewed hash")
    elif response_kind == "html":
        if media not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("HTML response media type conflicts with the route")
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("public HTML is not UTF-8") from error
        required = request.get("required_text_occurrences", [])
        if not isinstance(required, list) or not all(
            isinstance(value, str) and value.casefold() in text.casefold()
            for value in required
        ):
            raise ValueError("public HTML no longer matches its reviewed route")
    else:
        raise ValueError("unsupported public response kind")
    role = _required_text(request.get("expected_document_role"), "document role")
    occurrences = request.get("reviewed_target_occurrences", [])
    if not isinstance(occurrences, list) or not all(isinstance(row, dict) for row in occurrences):
        raise ValueError("reviewed target occurrences are malformed")
    if role in NON_TARGET_DOCUMENT_ROLES:
        status = "verified_non_target_document"
        candidates = []
        rejection_reason = "document_role_not_target_paper"
        occurrence_semantics = "citation_or_listing_only" if occurrences else "none"
    elif role == "target_paper_candidate":
        if not occurrences:
            raise ValueError("target-paper candidate lacks DOI/title continuity")
        status = "target_paper_candidate"
        candidates = [
            {
                "target_id": row["target_id"],
                "doi": row["doi"],
                "title": row["title"],
                "url": actual_url,
                "response_sha256": sha256,
                "document_role": role,
                "access_basis": request.get("access_basis"),
                "attachment_bytes_validated": True,
                "version_validated": False,
            }
            for row in occurrences
        ]
        rejection_reason = None
        occurrence_semantics = "target_identity_continuity"
    else:
        raise ValueError("unsupported reviewed document role")
    return {
        "request_id": request["request_id"],
        "route_id": request["route_id"],
        "status": status,
        "document_role": role,
        "response_kind": response_kind,
        "response_url": actual_url,
        "response_sha256": sha256,
        "response_bytes": len(body),
        "content_type": content_type,
        "author_orcids": request.get("author_orcids", []),
        "target_ids": request.get("target_ids", []),
        "target_occurrences": occurrences,
        "target_occurrence_count": len(occurrences),
        "occurrence_semantics": occurrence_semantics,
        "rejection_reason": rejection_reason,
        "lawful_attachment_candidates": candidates,
        "scientific_absence_proven": False,
        "scientific_field_evidence_admitted": False,
    }


def compile_author_hosted_report(
    manifest: JsonObject,
    raw_results: JsonObject,
    manifest_binding: JsonObject,
) -> JsonObject:
    """Compile public file observations while keeping all prior route states."""

    if manifest.get("contract") != AUTHOR_HOSTED_CONTRACT:
        raise ValueError("author-hosted route manifest is required")
    observations = raw_results.get("observations")
    if not isinstance(observations, dict):
        raise ValueError("author-hosted observations are missing")
    requests = manifest.get("public_requests", [])
    expected_ids = [row["request_id"] for row in requests]
    if set(observations) != set(expected_ids):
        raise ValueError("author-hosted observations do not match the manifest")
    rows = []
    attachments = []
    failed = []
    raw_refs = []
    binary_refs = []
    for request in requests:
        request_id = request["request_id"]
        observation = observations[request_id]
        if not isinstance(observation, dict):
            raise ValueError(f"{request_id} observation is malformed")
        if observation.get("request_id") != request_id:
            raise ValueError("observation request identity conflicts")
        envelope_ref = observation.get("raw_envelope_ref")
        if not isinstance(envelope_ref, dict):
            raise ValueError("raw envelope reference is missing")
        raw_refs.append({"request_id": request_id, **envelope_ref})
        binary_ref = observation.get("binary_file_ref")
        if isinstance(binary_ref, dict):
            binary_refs.append({"request_id": request_id, **binary_ref})
        if observation.get("status") == "public_route_failed":
            failed.append(
                {"request_id": request_id, "route_id": request["route_id"]}
            )
        candidates = observation.get("lawful_attachment_candidates", [])
        if not isinstance(candidates, list):
            raise ValueError("attachment candidates are malformed")
        attachments.extend(candidates)
        rows.append(observation)
    if attachments:
        next_route = "ingest-lawful-attachment-candidates"
        status = "author_hosted_routes_completed_attachments"
    elif failed:
        next_route = "retry-degraded-author-hosted-routes"
        status = "author_hosted_routes_completed_degraded"
    else:
        next_route = "compile-responsibility-aware-acquisition-options"
        status = "author_hosted_routes_completed_non_target_files"
    next_actions = {
        "ingest-lawful-attachment-candidates": (
            "Validate target-paper version and access basis, then parse only the admitted attachment bytes."
        ),
        "retry-degraded-author-hosted-routes": (
            "Retry only failed reviewed public routes while retaining successful non-target classifications."
        ),
        "compile-responsibility-aware-acquisition-options": (
            "Compile remaining credential, licensed-access, and optional contact routes for human responsibility without executing them or pausing other internal assurance work."
        ),
    }
    return {
        "schema_version": 1,
        "contract": AUTHOR_HOSTED_CONTRACT,
        "scope_id": manifest.get("scope_id"),
        "revision": manifest.get("revision"),
        "status": status,
        "source_manifest": _artifact(manifest_binding, "manifest_binding"),
        "author_count": manifest["author_count"],
        "target_count": manifest["target_count"],
        "route_count": manifest["route_count"],
        "executed_request_count": len(requests),
        "successful_request_count": len(requests) - len(failed),
        "failed_request_count": len(failed),
        "verified_non_target_document_count": sum(
            row.get("status") == "verified_non_target_document" for row in rows
        ),
        "binary_file_observation_count": sum(
            isinstance(row.get("binary_file_ref"), dict)
            and row.get("status") != "public_route_failed"
            for row in rows
        ),
        "citation_only_occurrence_count": sum(
            row.get("target_occurrence_count", 0)
            if row.get("occurrence_semantics") == "citation_or_listing_only"
            else 0
            for row in rows
        ),
        "lawful_attachment_candidate_count": len(attachments),
        "credential_required_route_count": manifest[
            "credential_required_route_count"
        ],
        "scope_inapplicable_route_count": manifest[
            "scope_inapplicable_route_count"
        ],
        "retained_retry_count": len(manifest.get("retained_retry_requests", [])),
        "observations": rows,
        "lawful_attachment_candidates": attachments,
        "raw_envelope_refs": raw_refs,
        "binary_file_refs": binary_refs,
        "routing": {
            "failed_requests": failed,
            "retained_retry_requests": manifest.get("retained_retry_requests", []),
            "credential_required_routes": manifest.get(
                "credential_required_routes", []
            ),
            "human_routes_untouched": manifest.get("routing", {}).get(
                "human_routes_untouched", []
            ),
            "next_internal_route": next_route,
            "pause_internal_work": False,
        },
        "scientific_absence_proven": False,
        "scientific_field_evidence_admitted": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "next_action": next_actions[next_route],
    }


def _fetched_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def fetch_public_request(
    request: JsonObject,
    *,
    attempts: int = 3,
    timeout: int = 30,
    open_url: Optional[Callable[..., object]] = None,
) -> JsonObject:
    """Fetch one reviewed public profile or file route."""

    if attempts < 1 or timeout < 1:
        raise ValueError("attempts and timeout must be positive")
    request_url = _canonical_url(request.get("request_url"))
    open_call = open_url or urllib.request.urlopen
    last_error = None
    last_status = None
    for attempt in range(1, attempts + 1):
        accept = (
            "application/pdf,application/octet-stream;q=0.8,*/*;q=0.1"
            if request.get("response_kind") == "pdf"
            else "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"
        )
        url_request = urllib.request.Request(
            request_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 "
                    "TianGong-LCA-Research-Agent/1.0"
                ),
                "Accept": accept,
            },
        )
        try:
            with open_call(url_request, timeout=timeout) as response:
                body = response.read()
                status = getattr(response, "status", 200)
                content_type = response.headers.get(
                    "Content-Type", "application/octet-stream"
                )
                response_url = _canonical_url(response.geturl())
            last_status = status
            if not 200 <= status < 300:
                raise urllib.error.HTTPError(
                    request_url, status, f"HTTP {status}", None, None
                )
            response_sha256 = hashlib.sha256(body).hexdigest()
            classification = classify_public_response(
                request,
                body,
                content_type=content_type,
                response_url=response_url,
                response_sha256=response_sha256,
            )
            return {
                "request_id": request["request_id"],
                "fetched_at": _fetched_at(),
                "http_status": status,
                "attempt_count": attempt,
                "content_type": content_type,
                "response_url": response_url,
                "response_sha256": response_sha256,
                "response_bytes": len(body),
                "error": None,
                "body": body,
                "classification": classification,
            }
        except urllib.error.HTTPError as error:
            last_status = error.code
            last_error = f"HTTP {error.code}: {error.reason}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            last_error = f"{type(error).__name__}: {error}"
    return {
        "request_id": request["request_id"],
        "fetched_at": _fetched_at(),
        "http_status": last_status,
        "attempt_count": attempts,
        "content_type": None,
        "response_url": None,
        "response_sha256": None,
        "response_bytes": 0,
        "error": last_error or "public author-hosted route failed without a response",
        "body": None,
        "classification": None,
    }


def _persist_response(
    request: JsonObject, response: JsonObject, raw_root: Path
) -> JsonObject:
    request_id = request["request_id"]
    body = response.get("body")
    classification = response.get("classification")
    binary_ref = None
    binary_relative = _binary_path(request_id) if request.get("response_kind") == "pdf" else None
    if binary_relative is not None:
        binary_path = raw_root / binary_relative
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        binary_content = body if isinstance(body, bytes) else b""
        binary_path.write_bytes(binary_content)
        binary_ref = {
            "path": f"raw/{binary_relative}",
            "sha256": hashlib.sha256(binary_content).hexdigest(),
            "bytes": len(binary_content),
            "materialized": isinstance(body, bytes),
        }
    envelope = {
        "request_id": request_id,
        "route_id": request["route_id"],
        "request_url": request["request_url"],
        "expected_document_role": request["expected_document_role"],
        "response_kind": request["response_kind"],
        "fetched_at": response.get("fetched_at"),
        "http_status": response.get("http_status"),
        "attempt_count": response.get("attempt_count"),
        "content_type": response.get("content_type"),
        "response_url": response.get("response_url"),
        "response_sha256": response.get("response_sha256"),
        "response_bytes": response.get("response_bytes"),
        "error": response.get("error"),
        "binary_file": binary_ref,
        "html_payload": (
            body.decode("utf-8")
            if request.get("response_kind") == "html" and isinstance(body, bytes)
            else None
        ),
    }
    envelope_relative = _envelope_path(request_id)
    envelope_path = raw_root / envelope_relative
    envelope_path.parent.mkdir(parents=True, exist_ok=True)
    envelope_bytes = _json_bytes(envelope)
    envelope_path.write_bytes(envelope_bytes)
    envelope_ref = {
        "path": f"raw/{envelope_relative}",
        "sha256": hashlib.sha256(envelope_bytes).hexdigest(),
        "bytes": len(envelope_bytes),
    }
    if isinstance(classification, dict):
        return {
            **classification,
            "raw_envelope_ref": envelope_ref,
            "binary_file_ref": binary_ref,
        }
    return {
        "request_id": request_id,
        "route_id": request["route_id"],
        "status": "public_route_failed",
        "document_role": request["expected_document_role"],
        "target_occurrence_count": 0,
        "occurrence_semantics": "unknown_due_to_route_failure",
        "lawful_attachment_candidates": [],
        "scientific_absence_proven": False,
        "scientific_field_evidence_admitted": False,
        "error": response.get("error"),
        "raw_envelope_ref": envelope_ref,
        "binary_file_ref": binary_ref,
    }


def execute_author_hosted_routes(
    manifest: JsonObject,
    raw_root: Path,
    manifest_binding: JsonObject,
    *,
    attempts: int = 3,
    timeout: int = 30,
) -> JsonObject:
    """Execute only the manifest's reviewed public author/file routes."""

    if manifest.get("contract") != AUTHOR_HOSTED_CONTRACT:
        raise ValueError("author-hosted route manifest is required")
    raw_root = raw_root.resolve()
    observations = {}
    for request in manifest.get("public_requests", []):
        response = fetch_public_request(request, attempts=attempts, timeout=timeout)
        observations[request["request_id"]] = _persist_response(
            request, response, raw_root
        )
    return compile_author_hosted_report(
        manifest, {"observations": observations}, manifest_binding
    )


def _read_json(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-binding", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)
    report = execute_author_hosted_routes(
        _read_json(args.manifest),
        args.raw_root,
        _read_json(args.manifest_binding),
        attempts=args.attempts,
        timeout=args.timeout,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(_json_bytes(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
