"""Execute and normalize public DOI and repository discovery routes."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence


JsonObject = Dict[str, object]

PUBLIC_DISCOVERY_CONTRACT = "lca_research_kernel.public_evidence_discovery/v1"
MANIFEST_CONTRACT = "lca_research_kernel.evidence_acquisition_manifest/v1"
SUPPORTED_PUBLIC_ROUTES = {
    "crossref-doi-metadata",
    "openalex-oa-locations",
    "semantic-scholar-oa-check",
    "exact-title-repository-search",
}
USER_AGENT = (
    "LCAResearchKernel/1.0 public-evidence-discovery "
    "(https://github.com/tiangong-lca)"
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


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


def _normalize_doi(value: object) -> str:
    text = _required_text(value, "DOI").lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.strip()


def _normalize_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", _required_text(value, "title"))
    return " ".join(text.casefold().split())


def _target_rows(manifest: JsonObject) -> List[JsonObject]:
    if manifest.get("contract") != MANIFEST_CONTRACT:
        raise ValueError("accepted evidence acquisition manifest is required")
    if manifest.get("status") != "lawful_evidence_acquisition_manifest_compiled":
        raise ValueError("evidence acquisition manifest is not current")
    targets = manifest.get("source_targets")
    routing = manifest.get("routing")
    if not isinstance(targets, list) or not targets or not isinstance(routing, dict):
        raise ValueError("manifest targets or routing are missing")
    public_routes = routing.get("public_routes")
    if not isinstance(public_routes, list) or not public_routes:
        raise ValueError("manifest contains no executable public routes")
    if set(public_routes) != SUPPORTED_PUBLIC_ROUTES:
        raise ValueError("public route set is unsupported or incomplete")
    result = []
    seen = set()
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("source target must be an object")
        target_id = _required_text(target.get("target_id"), "target_id")
        if target_id in seen or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in target_id):
            raise ValueError("target IDs must be unique filesystem-safe slugs")
        seen.add(target_id)
        identifiers = target.get("identifiers")
        route_ids = target.get("route_ids")
        if not isinstance(identifiers, dict) or not isinstance(route_ids, list):
            raise ValueError(f"{target_id} identifiers or routes are missing")
        if any(route not in route_ids for route in public_routes):
            raise ValueError(f"{target_id} does not declare every public route")
        result.append(target)
    return result


def build_public_discovery_requests(manifest: JsonObject) -> List[JsonObject]:
    """Build exact, bounded requests from public routes only."""

    targets = _target_rows(manifest)
    public_routes = sorted(manifest["routing"]["public_routes"])
    requests = []
    for target in targets:
        target_id = target["target_id"]
        title = _required_text(target.get("title"), f"{target_id}.title")
        doi = _normalize_doi(target["identifiers"].get("doi"))
        for route_id in public_routes:
            if route_id == "crossref-doi-metadata":
                url = "https://api.crossref.org/works/" + urllib.parse.quote(
                    doi, safe=""
                )
            elif route_id == "openalex-oa-locations":
                url = (
                    "https://api.openalex.org/works/https://doi.org/"
                    + urllib.parse.quote(doi, safe="/")
                )
            elif route_id == "semantic-scholar-oa-check":
                fields = (
                    "paperId,title,externalIds,isOpenAccess,openAccessPdf,"
                    "publicationDate,venue"
                )
                url = (
                    "https://api.semanticscholar.org/graph/v1/paper/DOI:"
                    + urllib.parse.quote(doi, safe="")
                    + "?"
                    + urllib.parse.urlencode({"fields": fields})
                )
            else:
                url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
                    {"search": title, "per-page": "10"}
                )
            requests.append(
                {
                    "target_id": target_id,
                    "route_id": route_id,
                    "requested_doi": doi,
                    "requested_title": title,
                    "request_url": url,
                }
            )
    return requests


def _fetch_one(request: JsonObject, *, attempts: int, timeout: int) -> JsonObject:
    payload = None
    error_text = None
    http_status = None
    response_sha256 = None
    response_bytes = 0
    attempt_count = 0
    for attempt in range(1, attempts + 1):
        attempt_count = attempt
        try:
            url_request = urllib.request.Request(
                request["request_url"],
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
            with urllib.request.urlopen(url_request, timeout=timeout) as response:
                body = response.read()
                http_status = response.getcode()
            response_sha256 = hashlib.sha256(body).hexdigest()
            response_bytes = len(body)
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("response JSON must be an object")
            error_text = None
            break
        except urllib.error.HTTPError as error:
            http_status = error.code
            error_text = f"HTTP {error.code}"
            if error.code not in {408, 429, 500, 502, 503, 504}:
                break
        except (urllib.error.URLError, TimeoutError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            error_text = f"{type(error).__name__}: {error}"
        if attempt < attempts:
            time.sleep(0.5 * attempt)
    return {
        "schema_version": 1,
        **request,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "http_status": http_status,
        "attempt_count": attempt_count,
        "response_sha256": response_sha256,
        "response_bytes": response_bytes,
        "error": error_text,
        "payload": payload,
    }


def fetch_public_discovery(
    manifest: JsonObject,
    raw_root: Path,
    *,
    attempts: int = 3,
    timeout: int = 20,
) -> Dict[str, Dict[str, JsonObject]]:
    """Execute bounded public reads and persist an envelope even on route failure."""

    if attempts < 1 or timeout < 1:
        raise ValueError("attempt and timeout limits must be positive")
    raw = {}
    for request in build_public_discovery_requests(manifest):
        envelope = _fetch_one(request, attempts=attempts, timeout=timeout)
        target_id = request["target_id"]
        route_id = request["route_id"]
        raw.setdefault(target_id, {})[route_id] = envelope
        path = raw_root / target_id / f"{route_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_json_bytes(envelope))
    return raw


def _identity_match(
    *,
    expected_doi: str,
    expected_title: str,
    returned_doi: object,
    returned_title: object,
) -> bool:
    return (
        _normalize_doi(returned_doi) == expected_doi
        and _normalize_title(returned_title) == _normalize_title(expected_title)
    )


def _location(value: object) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    source = value.get("source")
    if not isinstance(source, dict):
        source = {}
    return {
        "is_oa": value.get("is_oa") is True,
        "landing_page_url": value.get("landing_page_url"),
        "pdf_url": value.get("pdf_url"),
        "license": value.get("license"),
        "version": value.get("version"),
        "source_id": source.get("id"),
        "source_name": source.get("display_name"),
        "source_type": source.get("type"),
    }


def _crossref_summary(envelope: JsonObject, doi: str, title: str) -> JsonObject:
    payload = envelope["payload"]
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, dict):
        raise ValueError("Crossref response is malformed")
    titles = message.get("title")
    returned_title = titles[0] if isinstance(titles, list) and titles else None
    if not _identity_match(
        expected_doi=doi,
        expected_title=title,
        returned_doi=message.get("DOI"),
        returned_title=returned_title,
    ):
        raise ValueError("Crossref identity mismatch")
    links = []
    for row in message.get("link", []):
        if isinstance(row, dict):
            links.append(
                {
                    "url": row.get("URL"),
                    "content_type": row.get("content-type"),
                    "content_version": row.get("content-version"),
                    "classification": "candidate_content_link_not_acquired",
                }
            )
    return {
        "status": "metadata_identity_verified",
        "identity_match": True,
        "publisher": message.get("publisher"),
        "published": message.get("published"),
        "resource_links": links,
        "lawful_attachment_candidates": [],
        "no_hit": False,
        "scientific_absence_proven": False,
        "scientific_field_evidence_admitted": False,
    }


def _openalex_summary(envelope: JsonObject, doi: str, title: str) -> JsonObject:
    payload = envelope["payload"]
    if not isinstance(payload, dict) or not _identity_match(
        expected_doi=doi,
        expected_title=title,
        returned_doi=payload.get("doi"),
        returned_title=payload.get("title"),
    ):
        raise ValueError("OpenAlex identity mismatch")
    locations = [_location(row) for row in payload.get("locations", [])]
    candidates = [
        {
            "url": row["pdf_url"],
            "landing_page_url": row["landing_page_url"],
            "version": row["version"],
            "license": row["license"],
            "source_name": row["source_name"],
            "status": "lawful_attachment_candidate_requires_byte_ingest",
        }
        for row in locations
        if row.get("is_oa") and isinstance(row.get("pdf_url"), str) and row["pdf_url"]
    ]
    open_access = payload.get("open_access")
    if not isinstance(open_access, dict):
        open_access = {}
    return {
        "status": (
            "lawful_attachment_candidates_found"
            if candidates
            else "no_lawful_attachment_found"
        ),
        "identity_match": True,
        "work_id": payload.get("id"),
        "open_access": open_access,
        "best_oa_location": _location(payload.get("best_oa_location")),
        "locations": locations,
        "has_content": payload.get("has_content", {}),
        "lawful_attachment_candidates": candidates,
        "no_hit": not candidates,
        "scientific_absence_proven": False,
        "scientific_field_evidence_admitted": False,
    }


def _semantic_scholar_summary(
    envelope: JsonObject, doi: str, title: str
) -> JsonObject:
    payload = envelope["payload"]
    external_ids = payload.get("externalIds") if isinstance(payload, dict) else None
    returned_doi = external_ids.get("DOI") if isinstance(external_ids, dict) else None
    if not isinstance(payload, dict) or not _identity_match(
        expected_doi=doi,
        expected_title=title,
        returned_doi=returned_doi,
        returned_title=payload.get("title"),
    ):
        raise ValueError("Semantic Scholar identity mismatch")
    pdf = payload.get("openAccessPdf")
    if not isinstance(pdf, dict):
        pdf = {}
    pdf_url = pdf.get("url")
    candidates = []
    if isinstance(pdf_url, str) and pdf_url.strip():
        candidates.append(
            {
                "url": pdf_url,
                "version": None,
                "license": pdf.get("license"),
                "status": "lawful_attachment_candidate_requires_byte_ingest",
            }
        )
    return {
        "status": (
            "lawful_attachment_candidates_found"
            if candidates
            else "no_open_access_pdf_found"
        ),
        "identity_match": True,
        "paper_id": payload.get("paperId"),
        "is_open_access": payload.get("isOpenAccess") is True,
        "open_access_pdf": pdf,
        "lawful_attachment_candidates": candidates,
        "no_hit": not candidates,
        "scientific_absence_proven": False,
        "scientific_field_evidence_admitted": False,
    }


def _exact_title_summary(envelope: JsonObject, doi: str, title: str) -> JsonObject:
    payload = envelope["payload"]
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise ValueError("exact-title search response is malformed")
    matches = []
    candidates = []
    for result in results:
        if not isinstance(result, dict):
            continue
        try:
            is_match = _identity_match(
                expected_doi=doi,
                expected_title=title,
                returned_doi=result.get("doi"),
                returned_title=result.get("title"),
            )
        except ValueError:
            is_match = False
        if not is_match:
            continue
        locations = [_location(row) for row in result.get("locations", [])]
        matches.append(
            {
                "work_id": result.get("id"),
                "doi": result.get("doi"),
                "title": result.get("title"),
                "locations": locations,
            }
        )
        for row in locations:
            if (
                row.get("source_type") == "repository"
                and row.get("is_oa")
                and isinstance(row.get("pdf_url"), str)
                and row["pdf_url"]
            ):
                candidates.append(
                    {
                        "url": row["pdf_url"],
                        "landing_page_url": row["landing_page_url"],
                        "version": row["version"],
                        "license": row["license"],
                        "source_name": row["source_name"],
                        "status": "repository_attachment_candidate_requires_byte_ingest",
                    }
                )
    return {
        "status": (
            "repository_attachment_candidates_found"
            if candidates
            else "no_exact_repository_attachment_found"
        ),
        "identity_match": bool(matches),
        "exact_identity_matches": matches,
        "lawful_attachment_candidates": candidates,
        "no_hit": not candidates,
        "scientific_absence_proven": False,
        "scientific_field_evidence_admitted": False,
    }


def _route_summary(envelope: JsonObject, doi: str, title: str) -> JsonObject:
    route_id = envelope["route_id"]
    if envelope.get("error") or not isinstance(envelope.get("payload"), dict):
        return {
            "route_id": route_id,
            "status": "route_failed",
            "error": envelope.get("error") or "missing JSON payload",
            "http_status": envelope.get("http_status"),
            "attempt_count": envelope.get("attempt_count"),
            "lawful_attachment_candidates": [],
            "no_hit": False,
            "scientific_absence_proven": False,
            "scientific_field_evidence_admitted": False,
        }
    if route_id == "crossref-doi-metadata":
        summary = _crossref_summary(envelope, doi, title)
    elif route_id == "openalex-oa-locations":
        summary = _openalex_summary(envelope, doi, title)
    elif route_id == "semantic-scholar-oa-check":
        summary = _semantic_scholar_summary(envelope, doi, title)
    elif route_id == "exact-title-repository-search":
        summary = _exact_title_summary(envelope, doi, title)
    else:
        raise ValueError(f"unsupported route in raw result: {route_id}")
    return {
        "route_id": route_id,
        "request_url": envelope.get("request_url"),
        "fetched_at": envelope.get("fetched_at"),
        "http_status": envelope.get("http_status"),
        "attempt_count": envelope.get("attempt_count"),
        "response_sha256": envelope.get("response_sha256"),
        **summary,
    }


def compile_public_discovery_report(
    manifest: JsonObject,
    raw_results: Dict[str, Dict[str, JsonObject]],
    manifest_binding: JsonObject,
) -> JsonObject:
    """Normalize public route responses without admitting scientific field values."""

    targets = _target_rows(manifest)
    public_routes = sorted(manifest["routing"]["public_routes"])
    human_routes = sorted(manifest["routing"].get("human_routes", []))
    target_results = []
    failed_routes = []
    total_candidates = 0
    total_no_hits = 0
    successful_routes = 0
    raw_refs = []
    for target in targets:
        target_id = target["target_id"]
        doi = _normalize_doi(target["identifiers"].get("doi"))
        title = _required_text(target.get("title"), f"{target_id}.title")
        target_raw = raw_results.get(target_id)
        if not isinstance(target_raw, dict) or set(target_raw) != set(public_routes):
            raise ValueError(f"{target_id} raw results do not cover every public route")
        summaries = []
        identity_successes = []
        target_candidates = []
        target_no_hits = 0
        for route_id in public_routes:
            envelope = target_raw[route_id]
            if not isinstance(envelope, dict):
                raise ValueError(f"{target_id}/{route_id} envelope is malformed")
            if (
                envelope.get("target_id") != target_id
                or envelope.get("route_id") != route_id
                or _normalize_doi(envelope.get("requested_doi")) != doi
                or _normalize_title(envelope.get("requested_title"))
                != _normalize_title(title)
            ):
                raise ValueError(f"{target_id}/{route_id} request identity mismatch")
            summary = _route_summary(envelope, doi, title)
            summaries.append(summary)
            raw_path = f"raw/{target_id}/{route_id}.json"
            raw_refs.append(
                {
                    "target_id": target_id,
                    "route_id": route_id,
                    "path": raw_path,
                    "sha256": hashlib.sha256(_json_bytes(envelope)).hexdigest(),
                }
            )
            if summary["status"] == "route_failed":
                failed_routes.append(
                    {"target_id": target_id, "route_id": route_id}
                )
                continue
            successful_routes += 1
            if route_id != "exact-title-repository-search":
                identity_successes.append(summary.get("identity_match") is True)
            target_candidates.extend(summary["lawful_attachment_candidates"])
            target_no_hits += int(summary["no_hit"] is True)
        total_candidates += len(target_candidates)
        total_no_hits += target_no_hits
        required_fields = []
        for field in target.get("required_fields", []):
            if not isinstance(field, dict):
                raise ValueError(f"{target_id} required field is malformed")
            required_fields.append(
                {
                    "field_id": _required_text(field.get("field_id"), "field_id"),
                    "status": "unresolved",
                }
            )
        target_results.append(
            {
                "target_id": target_id,
                "doi": doi,
                "title": title,
                "identity_verified": bool(identity_successes) and all(identity_successes),
                "route_results": summaries,
                "lawful_attachment_candidates": target_candidates,
                "lawful_attachment_candidate_count": len(target_candidates),
                "no_hit_count": target_no_hits,
                "scientific_absence_proven": False,
                "required_fields": required_fields,
                "scientific_field_evidence_admitted": False,
            }
        )

    executed = len(targets) * len(public_routes)
    failed_count = len(failed_routes)
    if failed_count:
        status = "public_discovery_completed_degraded"
        next_internal_route = "expand-public-source-discovery"
    elif total_candidates:
        status = "public_discovery_completed_attachment_candidates"
        next_internal_route = "ingest-lawful-attachment-candidates"
    else:
        status = "public_discovery_completed_no_attachment"
        next_internal_route = "expand-public-source-discovery"
    return {
        "schema_version": 1,
        "contract": PUBLIC_DISCOVERY_CONTRACT,
        "scope_id": manifest.get("scope_id"),
        "revision": manifest.get("revision"),
        "status": status,
        "manifest": _artifact(manifest_binding, "manifest_binding"),
        "target_count": len(targets),
        "executed_route_count": executed,
        "successful_route_count": successful_routes,
        "failed_route_count": failed_count,
        "no_hit_count": total_no_hits,
        "lawful_attachment_candidate_count": total_candidates,
        "target_results": target_results,
        "raw_response_refs": raw_refs,
        "routing": {
            "retry_routes": failed_routes,
            "human_routes_untouched": human_routes,
            "next_internal_route": next_internal_route,
            "pause_internal_work": False,
        },
        "no_hit_policy": {
            "scientific_absence_proven": False,
            "meaning": "route/date-bounded public discovery result only",
        },
        "scientific_field_evidence_admitted": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "next_action": (
            "Retain degraded routes for an explicit bounded retry while expanding to additional public repository and author-hosted source classes."
            if failed_count
            else (
                "Hash and validate lawful attachment candidates before parsing."
                if total_candidates
                else "Expand public discovery to additional repository and author-hosted source classes while retaining the current no-hit reports."
            )
        ),
    }


def _read_json(path: Path) -> JsonObject:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-binding", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args(argv)
    manifest = _read_json(args.manifest)
    raw = fetch_public_discovery(
        manifest,
        args.raw_root,
        attempts=args.attempts,
        timeout=args.timeout,
    )
    report = compile_public_discovery_report(
        manifest,
        raw,
        _read_json(args.manifest_binding),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(_json_bytes(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
