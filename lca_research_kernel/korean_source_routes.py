"""Govern Korean scholarly source feasibility and exact public CRIS records."""

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
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence


JsonObject = Dict[str, object]

KOREAN_SOURCE_CONTRACT = "lca_research_kernel.korean_scholarly_source_routes/v1"
RECORD_EXPORT_CONTRACT = (
    "lca_research_kernel.exact_repository_record_export_probe/v1"
)

EXACT_PUBLIC_RECORDS = {
    "10.1016/j.cej.2012.05.058": {
        "route_id": "skku-pure-public-record",
        "service_name": "Sungkyunkwan University Research Portal",
        "service_owner": "Sungkyunkwan University",
        "request_url": (
            "https://pure.skku.edu/en/publications/"
            "carbon-coating-on-lithium-iron-phosphate-lifeposub4sub-comparison/"
        ),
        "repository_host": "pure.skku.edu",
        "official_evidence_url": "https://pure.skku.edu/",
    },
    "10.1016/j.supflu.2010.09.026": {
        "route_id": "korea-university-pure-public-record",
        "service_name": "Korea University Research Portal",
        "service_owner": "Korea University",
        "request_url": (
            "https://pure.korea.ac.kr/en/publications/"
            "small-capacity-decay-of-lithium-iron-phosphate-lifeposub4sub-synt/"
        ),
        "repository_host": "pure.korea.ac.kr",
        "official_evidence_url": "https://pure.korea.ac.kr/",
    },
}

REVIEWED_SERVICE_ROUTES = (
    {
        "route_id": "scienceon-paper-api",
        "service_name": "ScienceON Paper Open API",
        "service_owner": "Korea Institute of Science and Technology Information",
        "source_class": "national_scholarly_index_api",
        "route_status": "credential_required",
        "execution_policy": "human_credential_route",
        "credential_kind": "issued_api_key",
        "official_evidence_url": (
            "https://scienceon.kisti.re.kr/apigateway/api/way/service/arti/"
            "serviceArtiSearchApi.do"
        ),
        "applicability_reason": (
            "The service covers domestic and international papers, but its stable API "
            "requires registration and an issued key."
        ),
        "file_semantics": "metadata_or_provider_link_not_automatic_attachment",
    },
    {
        "route_id": "riss-overseas-article-api",
        "service_name": "RISS Overseas Article Open API",
        "service_owner": "Korea Education and Research Information Service",
        "source_class": "national_library_index_api",
        "route_status": "credential_required",
        "execution_policy": "human_credential_route",
        "credential_kind": "approved_api_key",
        "official_evidence_url": "https://librarian.riss.kr/",
        "applicability_reason": (
            "RISS covers overseas articles, but replayable API use requires an "
            "application and key."
        ),
        "file_semantics": "index_record_or_licensed_access_not_public_attachment",
    },
    {
        "route_id": "kci-article-api",
        "service_name": "Korea Citation Index Open API",
        "service_owner": "National Research Foundation of Korea",
        "source_class": "national_domestic_journal_index",
        "route_status": "scope_inapplicable",
        "execution_policy": "do_not_execute_for_current_targets",
        "credential_kind": None,
        "official_evidence_url": (
            "https://www.kci.go.kr/kciportal/po/openapi/openApiConnSamp.kci"
        ),
        "applicability_reason": (
            "The current targets are Elsevier foreign-journal articles, not KCI "
            "domestic-index source items."
        ),
        "file_semantics": "domestic_index_metadata_not_current_target_file_route",
    },
    {
        "route_id": "koreascience-journal-gateway",
        "service_name": "KoreaScience",
        "service_owner": "Korean Federation of Science and Technology Societies",
        "source_class": "korean_science_journal_gateway",
        "route_status": "scope_inapplicable",
        "execution_policy": "do_not_execute_for_current_targets",
        "credential_kind": None,
        "official_evidence_url": "https://koreascience.kr/",
        "applicability_reason": (
            "The current targets are publisher-hosted foreign journal articles, not "
            "KoreaScience journal content items."
        ),
        "file_semantics": "gateway_content_not_current_target_file_route",
    },
)


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


def compile_korean_source_manifest(
    record_export_report: JsonObject,
    record_export_binding: JsonObject,
) -> JsonObject:
    """Compile reviewed Korean source states and exact public CRIS requests."""

    if (
        record_export_report.get("contract") != RECORD_EXPORT_CONTRACT
        or record_export_report.get("routing", {}).get("next_internal_route")
        != "expand-korean-national-public-sources"
        or record_export_report.get("scientific_field_evidence_admitted") is not False
        or record_export_report.get("scientific_comparison_ready") is not False
        or record_export_report.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted record-export route is required")
    records = record_export_report.get("record_results")
    if not isinstance(records, list) or len(records) != 2:
        raise ValueError("exactly two metadata-only record results are required")
    targets = []
    public_routes = []
    requests = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("record-export target must be an object")
        if record.get("status") != "exact_record_metadata_only":
            raise ValueError("Korean source expansion requires metadata-only records")
        target_id = _required_text(record.get("target_id"), "target_id")
        if target_id in seen:
            raise ValueError(f"duplicate target_id: {target_id}")
        seen.add(target_id)
        doi = _normalize_doi(record.get("doi"))
        route = EXACT_PUBLIC_RECORDS.get(doi)
        if route is None:
            raise ValueError(f"no reviewed exact Korean public route for DOI {doi}")
        target = {
            "target_id": target_id,
            "doi": doi,
            "title": _required_text(record.get("title"), f"{target_id}.title"),
            "prior_record_url": _canonical_url(record.get("record_url")),
        }
        targets.append(target)
        route_row = {
            **route,
            "source_class": "official_university_research_portal",
            "route_status": "public_exact_record",
            "execution_policy": "bounded_public_read",
            "credential_kind": None,
            "applicability_reason": "DOI/title-exact official university CRIS record",
            "file_semantics": "explicit_pure_document_link_only",
            "target_ids": [target_id],
        }
        public_routes.append(route_row)
        requests.append(
            {
                "request_id": f"{route['route_id']}:{target_id}",
                "route_id": route["route_id"],
                "target_id": target_id,
                "requested_doi": doi,
                "requested_title": target["title"],
                "request_url": _canonical_url(route["request_url"]),
                "repository_host": route["repository_host"],
                "response_kind": "html",
            }
        )
    targets.sort(key=lambda row: row["target_id"])
    public_routes.sort(key=lambda row: row["route_id"])
    requests.sort(key=lambda row: row["request_id"])
    target_ids = [row["target_id"] for row in targets]
    reviewed = [
        {**row, "target_ids": target_ids}
        for row in REVIEWED_SERVICE_ROUTES
    ]
    registry = sorted(reviewed + public_routes, key=lambda row: row["route_id"])
    retries = record_export_report.get("routing", {}).get(
        "retained_retry_requests", []
    )
    if not isinstance(retries, list) or not all(isinstance(row, dict) for row in retries):
        raise ValueError("retained retry requests are malformed")
    binding = _artifact(record_export_binding, "record_export_binding")
    return {
        "schema_version": 1,
        "contract": KOREAN_SOURCE_CONTRACT,
        "scope_id": _required_text(record_export_report.get("scope_id"), "scope_id"),
        "revision": _required_text(record_export_report.get("revision"), "revision"),
        "status": "korean_scholarly_source_routes_compiled",
        "record_export_report": binding,
        "target_count": len(targets),
        "record_targets": targets,
        "source_registry_count": len(registry),
        "credential_required_route_count": sum(
            row["route_status"] == "credential_required" for row in registry
        ),
        "scope_inapplicable_route_count": sum(
            row["route_status"] == "scope_inapplicable" for row in registry
        ),
        "public_exact_route_count": sum(
            row["route_status"] == "public_exact_record" for row in registry
        ),
        "source_registry": registry,
        "public_requests": requests,
        "retained_retry_requests": retries,
        "routing": {
            "human_routes_untouched": record_export_report.get("routing", {}).get(
                "human_routes_untouched", []
            ),
            "next_internal_route": "execute-korean-public-record-routes",
            "pause_internal_work": False,
        },
        "scientific_field_evidence_admitted": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
    }


def _raw_path(request_id: str) -> str:
    return f"records/{urllib.parse.quote(request_id, safe='')}.json"


def expected_korean_route_output_paths(manifest: JsonObject) -> List[str]:
    requests = manifest.get("public_requests")
    if not isinstance(requests, list):
        raise ValueError("public requests are malformed")
    return [
        _raw_path(_required_text(request.get("request_id"), "request_id"))
        for request in requests
        if isinstance(request, dict)
    ]


class _PureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: Dict[str, List[str]] = {}
        self.links: List[JsonObject] = []
        self.abstract_parts: List[str] = []
        self._abstract_depth = 0
        self._current_link: Optional[JsonObject] = None

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        values = {str(key).casefold(): value for key, value in attrs}
        lower_tag = tag.casefold()
        entering_abstract = False
        if lower_tag == "div":
            classes = str(values.get("class") or "").casefold().split()
            entering_abstract = any("abstractportal" in value for value in classes)
            if entering_abstract and self._abstract_depth == 0:
                self._abstract_depth = 1
        if self._abstract_depth and not entering_abstract:
            self._abstract_depth += 1
        if lower_tag == "meta":
            name = values.get("name") or values.get("property")
            content = values.get("content")
            if isinstance(name, str) and isinstance(content, str):
                self.meta.setdefault(name.casefold(), []).append(content.strip())
        if lower_tag == "a" and isinstance(values.get("href"), str):
            self._current_link = {
                "href": values["href"].strip(),
                "classes": str(values.get("class") or "").casefold().split(),
                "text_parts": [],
            }

    def handle_data(self, data: str) -> None:
        if self._abstract_depth:
            self.abstract_parts.append(data)
        if self._current_link is not None:
            self._current_link["text_parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        lower_tag = tag.casefold()
        if lower_tag == "a" and self._current_link is not None:
            self.links.append(
                {
                    "href": self._current_link["href"],
                    "classes": self._current_link["classes"],
                    "text": " ".join(
                        "".join(self._current_link["text_parts"]).split()
                    ),
                }
            )
            self._current_link = None
        if self._abstract_depth:
            self._abstract_depth -= 1


def analyze_pure_record(request: JsonObject, html_text: str) -> JsonObject:
    """Verify an exact Pure record and classify explicit document links only."""

    if not isinstance(html_text, str):
        raise ValueError("Pure record payload must be HTML text")
    parser = _PureParser()
    parser.feed(html_text)
    parser.close()
    titles = parser.meta.get("citation_title", [])
    if not any(
        _normalize_title(value) == _normalize_title(request.get("requested_title"))
        for value in titles
    ):
        raise ValueError("Pure record title conflicts with the accepted target")
    dois = parser.meta.get("citation_doi", [])
    if not any(
        _normalize_doi(value) == _normalize_doi(request.get("requested_doi"))
        for value in dois
    ):
        raise ValueError("Pure record DOI conflicts with the accepted target")

    record_url = _canonical_url(request.get("request_url"))
    candidates = []
    seen = set()

    def add_candidate(url: str, candidate_kind: str) -> None:
        absolute = _canonical_url(urllib.parse.urljoin(record_url, url))
        if absolute in seen:
            return
        seen.add(absolute)
        candidates.append(
            {
                "target_id": request["target_id"],
                "doi": request["requested_doi"],
                "title": request["requested_title"],
                "record_url": record_url,
                "url": absolute,
                "candidate_kind": candidate_kind,
                "access_basis": "public_document_representation_on_exact_cris_record",
                "attachment_bytes_validated": False,
            }
        )

    for value in parser.meta.get("citation_pdf_url", []):
        add_candidate(value, "pure_citation_pdf_url")
    for link in parser.links:
        href = link["href"]
        if not isinstance(href, str) or not href or href.casefold().startswith(
            ("javascript:", "mailto:")
        ):
            continue
        parsed_path = urllib.parse.urlsplit(
            urllib.parse.urljoin(record_url, href)
        ).path.casefold()
        explicit = "document-link" in link["classes"]
        file_path = "/files/" in parsed_path or "/portal/files/" in parsed_path
        if explicit or (file_path and parsed_path.endswith(".pdf")):
            add_candidate(href, "pure_explicit_document_link")
    abstract = " ".join(" ".join(parser.abstract_parts).split())
    abstract_hash = hashlib.sha256(abstract.encode("utf-8")).hexdigest() if abstract else None
    return {
        "target_id": request["target_id"],
        "route_id": request["route_id"],
        "doi": request["requested_doi"],
        "title": request["requested_title"],
        "record_url": record_url,
        "status": (
            "exact_cris_record_public_documents"
            if candidates
            else "exact_cris_record_metadata_only"
        ),
        "metadata_abstract_sha256": abstract_hash,
        "metadata_abstract_admitted_as_field_evidence": False,
        "public_document_candidates": candidates,
    }


def _failed(envelope: object) -> bool:
    return not isinstance(envelope, dict) or envelope.get("error") is not None


def compile_korean_source_report(
    manifest: JsonObject,
    raw_results: JsonObject,
    manifest_binding: JsonObject,
) -> JsonObject:
    """Compile executable, credential, scope, record, and file route states."""

    if manifest.get("contract") != KOREAN_SOURCE_CONTRACT:
        raise ValueError("Korean source route manifest is required")
    responses = raw_results.get("responses")
    if not isinstance(responses, dict):
        raise ValueError("raw Korean source responses are missing")
    requests = manifest.get("public_requests", [])
    expected_ids = [request["request_id"] for request in requests]
    if set(responses) != set(expected_ids):
        raise ValueError("raw Korean source responses do not match the manifest")
    binding = _artifact(manifest_binding, "manifest_binding")
    results = []
    attachments = []
    failed = []
    raw_refs = []
    exact_count = 0
    metadata_only = 0
    for request in requests:
        request_id = request["request_id"]
        envelope = responses[request_id]
        if not isinstance(envelope, dict):
            raise ValueError(f"{request_id} envelope is malformed")
        raw_refs.append(
            {
                "request_id": request_id,
                "path": f"raw/{_raw_path(request_id)}",
                "sha256": hashlib.sha256(_json_bytes(envelope)).hexdigest(),
            }
        )
        if _failed(envelope):
            failed.append({"request_id": request_id, "route_id": request["route_id"]})
            results.append(
                {
                    "target_id": request["target_id"],
                    "route_id": request["route_id"],
                    "status": "public_cris_route_failed",
                    "public_document_candidates": [],
                }
            )
            continue
        if _canonical_url(envelope.get("response_url")) != _canonical_url(
            request["request_url"]
        ):
            raise ValueError("Pure response URL conflicts with the exact route")
        result = analyze_pure_record(request, envelope.get("payload"))
        exact_count += 1
        if result["status"] == "exact_cris_record_metadata_only":
            metadata_only += 1
        attachments.extend(result["public_document_candidates"])
        results.append(result)

    if attachments:
        next_route = "ingest-lawful-attachment-candidates"
        status = "korean_source_routes_completed_attachments"
    elif failed:
        next_route = "retry-degraded-korean-public-routes"
        status = "korean_source_routes_completed_degraded"
    else:
        next_route = "expand-author-hosted-public-files"
        status = "korean_source_routes_completed_metadata_only"
    next_actions = {
        "ingest-lawful-attachment-candidates": (
            "Validate exact CRIS document bytes, hashes, media types, versions, and public access bases before parsing."
        ),
        "retry-degraded-korean-public-routes": (
            "Retry only failed public CRIS reads while retaining credential and scope decisions unchanged."
        ),
        "expand-author-hosted-public-files": (
            "Compile bounded exact-author public profile and hosted-file routes without contacting authors or accepting unrelated citation files."
        ),
    }
    return {
        "schema_version": 1,
        "contract": KOREAN_SOURCE_CONTRACT,
        "scope_id": manifest.get("scope_id"),
        "revision": manifest.get("revision"),
        "status": status,
        "source_manifest": binding,
        "target_count": manifest["target_count"],
        "source_registry_count": manifest["source_registry_count"],
        "credential_required_route_count": manifest[
            "credential_required_route_count"
        ],
        "scope_inapplicable_route_count": manifest[
            "scope_inapplicable_route_count"
        ],
        "public_exact_route_count": manifest["public_exact_route_count"],
        "executed_request_count": len(requests),
        "successful_request_count": len(requests) - len(failed),
        "failed_request_count": len(failed),
        "exact_cris_record_count": exact_count,
        "metadata_only_record_count": metadata_only,
        "lawful_attachment_candidate_count": len(attachments),
        "retained_retry_count": len(manifest.get("retained_retry_requests", [])),
        "source_registry": manifest["source_registry"],
        "record_results": results,
        "lawful_attachment_candidates": attachments,
        "raw_response_refs": raw_refs,
        "routing": {
            "failed_requests": failed,
            "retained_retry_requests": manifest.get("retained_retry_requests", []),
            "credential_required_routes": [
                row["route_id"]
                for row in manifest["source_registry"]
                if row["route_status"] == "credential_required"
            ],
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
    timeout: int = 20,
    open_url: Optional[Callable[..., object]] = None,
) -> JsonObject:
    """Fetch one bounded exact public CRIS HTML record."""

    if attempts < 1 or timeout < 1:
        raise ValueError("attempts and timeout must be positive")
    open_call = open_url or urllib.request.urlopen
    request_url = _canonical_url(request.get("request_url"))
    expected_host = _required_text(request.get("repository_host"), "repository_host")
    last_error = None
    last_status = None
    for attempt in range(1, attempts + 1):
        url_request = urllib.request.Request(
            request_url,
            headers={
                "User-Agent": "TianGong-LCA-Research-Agent/1.0 (public exact CRIS probe)",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
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
            if urllib.parse.urlsplit(response_url).hostname != expected_host:
                raise ValueError("public CRIS redirect left the reviewed host")
            try:
                payload = body.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("public CRIS response is not UTF-8") from error
            return {
                **request,
                "fetched_at": _fetched_at(),
                "http_status": status,
                "attempt_count": attempt,
                "content_type": content_type,
                "response_url": response_url,
                "response_sha256": hashlib.sha256(body).hexdigest(),
                "response_bytes": len(body),
                "error": None,
                "payload": payload,
            }
        except urllib.error.HTTPError as error:
            last_status = error.code
            last_error = f"HTTP {error.code}: {error.reason}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            last_error = f"{type(error).__name__}: {error}"
    return {
        **request,
        "fetched_at": _fetched_at(),
        "http_status": last_status,
        "attempt_count": attempts,
        "content_type": None,
        "response_url": None,
        "response_sha256": None,
        "response_bytes": 0,
        "error": last_error or "public CRIS route failed without a response",
        "payload": None,
    }


def execute_korean_source_routes(
    manifest: JsonObject,
    raw_root: Path,
    manifest_binding: JsonObject,
    *,
    attempts: int = 3,
    timeout: int = 20,
) -> JsonObject:
    """Execute only the manifest's public exact-record reads."""

    if manifest.get("contract") != KOREAN_SOURCE_CONTRACT:
        raise ValueError("Korean source route manifest is required")
    raw_root = raw_root.resolve()
    responses = {}
    for request in manifest.get("public_requests", []):
        envelope = fetch_public_request(
            request, attempts=attempts, timeout=timeout
        )
        responses[request["request_id"]] = envelope
        path = raw_root / _raw_path(request["request_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_json_bytes(envelope))
    return compile_korean_source_report(
        manifest, {"responses": responses}, manifest_binding
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
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args(argv)
    report = execute_korean_source_routes(
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
