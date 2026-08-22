"""Resolve exact author and institution anchors through public routes."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import urllib.parse
import urllib.error
import urllib.request
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence


JsonObject = Dict[str, object]

AUTHOR_INSTITUTION_CONTRACT = (
    "lca_research_kernel.author_institution_public_routes/v1"
)
SOURCE_EXPANSION_CONTRACT = "lca_research_kernel.public_source_expansion/v1"

ORCID_SECTIONS = ("person", "works", "employments")

INSTITUTION_REGISTRY = {
    "Korea Institute of Science and Technology": {
        "resolution_status": "verified_public_repository",
        "repository_id": "kist-library-institutional-repository",
        "repository_name": "KIST Library Institutional Repository",
        "repository_home": "https://pubs.kist.re.kr/",
        "repository_host": "pubs.kist.re.kr",
        "verification_url": "https://pubs.kist.re.kr/",
        "adapter_kind": "dspace-kist-html",
        "search_url": "https://pubs.kist.re.kr/simple-search?query={query}",
    },
    "Seoul National University": {
        "resolution_status": "verified_public_repository",
        "repository_id": "snu-s-space",
        "repository_name": "SNU Open Repository and Archive",
        "repository_home": "https://s-space.snu.ac.kr/",
        "repository_host": "s-space.snu.ac.kr",
        "verification_url": "https://s-space.snu.ac.kr/about/side_02.jsp",
        "adapter_kind": "dcollection-snu-html",
        "search_url": (
            "https://s-space.snu.ac.kr/srch/srchResultList?"
            "searchWhere1=all&searchOption=em&searchKeyWord1={query}"
        ),
    },
    "Korea University": {
        "resolution_status": "verified_public_repository",
        "repository_id": "korea-university-dcollection",
        "repository_name": "Korea University dCollection",
        "repository_home": "https://dcollection.korea.ac.kr/",
        "repository_host": "dcollection.korea.ac.kr",
        "verification_url": (
            "https://library.korea.ac.kr/datause/archiving/?lang=en"
        ),
        "adapter_kind": "dcollection-korea-html",
        "search_url": (
            "https://dcollection.korea.ac.kr/srch/srchResultList?"
            "searchWhere1=all&searchOption=em&searchKeyWord1={query}"
        ),
    },
    "Korean Association Of Science and Technology Studies": {
        "resolution_status": "no_verified_repository_route",
        "organization_home": "https://stskorea.org/",
        "verification_url": "https://stskorea.org/",
        "reason": (
            "The accepted organization has a public website but no verified "
            "institutional scholarly repository route."
        ),
    },
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


def _normalize_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", _required_text(value, "author name"))
    return " ".join(sorted(re.findall(r"[\w]+", text.casefold())))


def _normalize_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", _required_text(value, "title"))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"(?<=[A-Za-z])\((\d+)\)", r"\1", text)
    return " ".join(re.findall(r"[\w]+", text.casefold()))


def _normalize_doi(value: object) -> str:
    text = _required_text(value, "DOI").lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.strip()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _canonical_url(value: object) -> str:
    text = _required_text(value, "URL")
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be public HTTP(S)")
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            parsed.query,
            "",
        )
    )


def _raw_primary_path(request_id: str) -> str:
    return f"primary/{urllib.parse.quote(request_id, safe='')}.json"


def _raw_detail_path(request_id: str) -> str:
    return f"detail/{urllib.parse.quote(request_id, safe='')}.json"


def expected_route_output_paths(manifest: JsonObject) -> List[str]:
    requests = manifest.get("author_requests", []) + manifest.get(
        "institution_requests", []
    )
    if not all(isinstance(row, dict) for row in requests):
        raise ValueError("route manifest requests are malformed")
    return [
        *[_raw_primary_path(row["request_id"]) for row in requests],
        *[
            _raw_detail_path(row["request_id"])
            for row in manifest.get("institution_requests", [])
        ],
    ]


class _RepositoryHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: Dict[str, List[str]] = {}
        self.links: List[JsonObject] = []
        self._current_href = None
        self._current_text: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        attributes = {str(key).lower(): value for key, value in attrs}
        if tag.lower() == "meta":
            name = attributes.get("name") or attributes.get("property")
            content = attributes.get("content")
            if isinstance(name, str) and isinstance(content, str):
                self.meta.setdefault(name.casefold(), []).append(content.strip())
        if tag.lower() == "a" and isinstance(attributes.get("href"), str):
            self._current_href = attributes["href"]
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._current_href is not None:
            self.links.append(
                {
                    "href": self._current_href,
                    "text": " ".join("".join(self._current_text).split()),
                }
            )
            self._current_href = None
            self._current_text = []


def _parse_html(value: object) -> _RepositoryHtmlParser:
    if not isinstance(value, str):
        raise ValueError("repository payload must be HTML text")
    parser = _RepositoryHtmlParser()
    parser.feed(value)
    parser.close()
    return parser


def extract_exact_repository_detail_candidates(
    request: JsonObject, html_text: str
) -> List[JsonObject]:
    """Select exact-title detail links without leaving the verified host."""

    parser = _parse_html(html_text)
    expected_title = _normalize_title(request.get("requested_title"))
    expected_host = _required_text(
        request.get("repository_host"), "repository_host"
    ).lower()
    result = []
    seen = set()
    for link in parser.links:
        text = link.get("text")
        if (
            not isinstance(text, str)
            or not text.strip()
            or _normalize_title(text) != expected_title
        ):
            continue
        absolute = _canonical_url(
            urllib.parse.urljoin(request["request_url"], link["href"])
        )
        if urllib.parse.urlsplit(absolute).hostname != expected_host:
            raise ValueError("exact detail URL left the verified repository host")
        if absolute in seen:
            continue
        seen.add(absolute)
        result.append(
            {
                "target_id": request["target_id"],
                "repository_id": request["repository_id"],
                "url": absolute,
                "search_result_title": text,
            }
        )
    return result


def _challenge_cookie(html_text: str) -> Optional[str]:
    if "data-name=" not in html_text or "data-token=" not in html_text:
        return None
    name_match = re.search(
        r"data-name\s*=\s*['\"]([^'\"]+)['\"]", html_text, flags=re.IGNORECASE
    )
    token_match = re.search(
        r"data-token\s*=\s*['\"]([^'\"]+)['\"]", html_text, flags=re.IGNORECASE
    )
    if not name_match or not token_match:
        return None
    name = name_match.group(1).strip()
    token = token_match.group(1).strip()
    if not name or not token or any(character in name + token for character in "\r\n;"):
        raise ValueError("unsafe repository JavaScript challenge cookie")
    return f"{name}={token}"


def _fetched_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_public_request(
    request: JsonObject,
    *,
    attempts: int = 3,
    timeout: int = 20,
    open_url: Optional[Callable] = None,
) -> JsonObject:
    """Fetch one public JSON/HTML route with a bounded browser-cookie retry."""

    if attempts < 1 or timeout < 1:
        raise ValueError("attempt and timeout limits must be positive")
    request_url = _canonical_url(request.get("request_url"))
    response_kind = _required_text(request.get("response_kind"), "response_kind")
    if response_kind not in {"json", "html"}:
        raise ValueError("response_kind must be json or html")
    open_call = open_url or urllib.request.urlopen
    last_error = None
    last_status = None
    challenge_retry_count = 0
    for attempt in range(1, attempts + 1):
        cookie = None
        for challenge_step in range(2):
            headers = {
                "User-Agent": (
                    "TianGong-LCA-Research-Kernel/1.0 "
                    "(+public-evidence-read; no-contact)"
                ),
                "Accept": (
                    "application/vnd.orcid+json, application/json;q=0.9"
                    if response_kind == "json"
                    else "text/html,application/xhtml+xml;q=0.9"
                ),
            }
            if cookie:
                headers["Cookie"] = cookie
            url_request = urllib.request.Request(request_url, headers=headers)
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
                try:
                    text = body.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ValueError("public route response is not UTF-8") from error
                if response_kind == "html":
                    challenge = _challenge_cookie(text)
                    if challenge:
                        if challenge_step or challenge_retry_count:
                            raise ValueError(
                                "repository JavaScript challenge persisted after retry"
                            )
                        cookie = challenge
                        challenge_retry_count += 1
                        continue
                    payload = text
                else:
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError as error:
                        raise ValueError("public JSON route returned invalid JSON") from error
                    if not isinstance(payload, dict):
                        raise ValueError("public JSON route must return an object")
                return {
                    **request,
                    "fetched_at": _fetched_at(),
                    "http_status": status,
                    "attempt_count": attempt,
                    "challenge_retry_count": challenge_retry_count,
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
                break
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                last_error = f"{type(error).__name__}: {error}"
                break
    return {
        **request,
        "fetched_at": _fetched_at(),
        "http_status": last_status,
        "attempt_count": attempts,
        "challenge_retry_count": challenge_retry_count,
        "content_type": None,
        "response_url": None,
        "response_sha256": None,
        "response_bytes": 0,
        "error": last_error or "public route failed without a response",
        "payload": None,
    }


def _normalize_orcid(value: object) -> str:
    text = _required_text(value, "ORCID").upper()
    text = text.removeprefix("HTTPS://ORCID.ORG/")
    compact = text.replace("-", "")
    if not re.fullmatch(r"\d{15}[\dX]", compact):
        raise ValueError(f"invalid ORCID syntax: {value}")
    total = 0
    for character in compact[:15]:
        total = (total + int(character)) * 2
    remainder = total % 11
    result = (12 - remainder) % 11
    expected = "X" if result == 10 else str(result)
    if compact[-1] != expected:
        raise ValueError(f"invalid ORCID checksum: {value}")
    return "-".join(
        (compact[0:4], compact[4:8], compact[8:12], compact[12:16])
    )


def _target_index(report: JsonObject) -> Dict[str, JsonObject]:
    targets = report.get("target_results")
    if not isinstance(targets, list) or not targets:
        raise ValueError("source expansion target results are missing")
    result = {}
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("source expansion target must be an object")
        target_id = _required_text(target.get("target_id"), "target_id")
        if target_id in result:
            raise ValueError(f"duplicate target_id: {target_id}")
        result[target_id] = {
            "target_id": target_id,
            "doi": _required_text(target.get("doi"), f"{target_id}.doi").lower(),
            "title": _required_text(target.get("title"), f"{target_id}.title"),
        }
    return result


def compile_author_institution_manifest(
    source_expansion_report: JsonObject,
    source_expansion_binding: JsonObject,
) -> JsonObject:
    """Compile distinct public author and repository routes from exact anchors."""

    if (
        source_expansion_report.get("contract") != SOURCE_EXPANSION_CONTRACT
        or source_expansion_report.get("routing", {}).get("next_internal_route")
        != "derive-author-institution-public-routes"
        or source_expansion_report.get("scientific_field_evidence_admitted")
        is not False
        or source_expansion_report.get("scientific_comparison_ready") is not False
        or source_expansion_report.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted source-expansion route is required")
    targets = _target_index(source_expansion_report)

    authors: Dict[str, JsonObject] = {}
    anchors = source_expansion_report.get("author_route_anchors")
    if not isinstance(anchors, list) or not anchors:
        raise ValueError("source expansion author anchors are missing")
    for anchor in anchors:
        if not isinstance(anchor, dict):
            raise ValueError("author anchor must be an object")
        target_id = _required_text(anchor.get("target_id"), "author target_id")
        if target_id not in targets:
            raise ValueError(f"unknown author target_id: {target_id}")
        orcid = _normalize_orcid(anchor.get("orcid"))
        name = _required_text(anchor.get("full_name"), f"{orcid}.full_name")
        current = authors.setdefault(
            orcid,
            {
                "orcid": orcid,
                "expected_names": [],
                "target_ids": [],
            },
        )
        normalized_names = {_normalize_name(row) for row in current["expected_names"]}
        if current["expected_names"] and _normalize_name(name) not in normalized_names:
            raise ValueError(f"conflicting accepted names for ORCID {orcid}")
        if name not in current["expected_names"]:
            current["expected_names"].append(name)
        if target_id not in current["target_ids"]:
            current["target_ids"].append(target_id)

    institutions: Dict[str, JsonObject] = {}
    institution_anchors = source_expansion_report.get("institution_route_anchors")
    if not isinstance(institution_anchors, list) or not institution_anchors:
        raise ValueError("source expansion institution anchors are missing")
    for anchor in institution_anchors:
        if not isinstance(anchor, dict):
            raise ValueError("institution anchor must be an object")
        target_id = _required_text(anchor.get("target_id"), "institution target_id")
        if target_id not in targets:
            raise ValueError(f"unknown institution target_id: {target_id}")
        name = _required_text(anchor.get("name"), "institution name")
        if name not in INSTITUTION_REGISTRY:
            raise ValueError(f"unreviewed institution route: {name}")
        current = institutions.setdefault(
            name,
            {
                "name": name,
                "accepted_acronyms": [],
                "target_ids": [],
                **INSTITUTION_REGISTRY[name],
            },
        )
        acronym = anchor.get("acronym")
        if isinstance(acronym, str) and acronym.strip():
            current["accepted_acronyms"].append(acronym.strip())
        if target_id not in current["target_ids"]:
            current["target_ids"].append(target_id)

    author_targets = []
    author_requests = []
    for orcid, author in sorted(authors.items()):
        normalized = {
            **author,
            "expected_names": sorted(set(author["expected_names"])),
            "target_ids": sorted(author["target_ids"]),
        }
        author_targets.append(normalized)
        for section in ORCID_SECTIONS:
            author_requests.append(
                {
                    "request_id": f"orcid:{orcid}:{section}",
                    "route_id": f"orcid-public-{section}",
                    "orcid": orcid,
                    "section": section,
                    "target_ids": normalized["target_ids"],
                    "expected_names": normalized["expected_names"],
                    "request_url": f"https://pub.orcid.org/v3.0/{orcid}/{section}",
                    "response_kind": "json",
                    "official_documentation_url": (
                        "https://info.orcid.org/documentation/api-tutorials/"
                        "api-tutorial-read-data-on-a-record/"
                    ),
                }
            )

    institution_targets = []
    institution_requests = []
    for name, institution in sorted(institutions.items()):
        normalized = {
            **institution,
            "accepted_acronyms": sorted(set(institution["accepted_acronyms"])),
            "target_ids": sorted(institution["target_ids"]),
        }
        institution_targets.append(normalized)
        if normalized["resolution_status"] != "verified_public_repository":
            continue
        for target_id in normalized["target_ids"]:
            target = targets[target_id]
            query = urllib.parse.quote(target["title"], safe="")
            institution_requests.append(
                {
                    "request_id": (
                        f"institution:{normalized['repository_id']}:{target_id}"
                    ),
                    "route_id": "institution-public-repository-search",
                    "institution_name": name,
                    "repository_id": normalized["repository_id"],
                    "repository_host": normalized["repository_host"],
                    "adapter_kind": normalized["adapter_kind"],
                    "target_id": target_id,
                    "requested_doi": target["doi"],
                    "requested_title": target["title"],
                    "request_url": normalized["search_url"].format(query=query),
                    "response_kind": "html",
                    "official_verification_url": normalized["verification_url"],
                }
            )

    unresolved_count = sum(
        row["resolution_status"] == "no_verified_repository_route"
        for row in institution_targets
    )
    return {
        "schema_version": 1,
        "contract": AUTHOR_INSTITUTION_CONTRACT,
        "scope_id": source_expansion_report.get("scope_id"),
        "revision": source_expansion_report.get("revision"),
        "status": "author_institution_public_route_manifest_compiled",
        "source_expansion": _artifact(
            source_expansion_binding, "source_expansion_binding"
        ),
        "target_index": [targets[key] for key in sorted(targets)],
        "author_target_count": len(author_targets),
        "institution_target_count": len(institution_targets),
        "orcid_request_count": len(author_requests),
        "institution_search_request_count": len(institution_requests),
        "unresolved_institution_route_count": unresolved_count,
        "author_targets": author_targets,
        "institution_targets": institution_targets,
        "author_requests": author_requests,
        "institution_requests": institution_requests,
        "routing": {
            "human_routes_untouched": sorted(
                source_expansion_report.get("routing", {}).get(
                    "human_routes_untouched", []
                )
            ),
            "next_internal_route": "execute-author-institution-public-routes",
            "pause_internal_work": False,
        },
        "scientific_field_evidence_admitted": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
    }


def _envelope_failed(envelope: object) -> bool:
    return not isinstance(envelope, dict) or bool(envelope.get("error")) or not (
        isinstance(envelope.get("http_status"), int)
        and 200 <= envelope["http_status"] < 300
    )


def _orcid_public_name(payload: object) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), dict):
        raise ValueError("ORCID person response is malformed")
    name = payload["name"]

    def value(key: str) -> str:
        row = name.get(key)
        if not isinstance(row, dict):
            return ""
        current = row.get("value")
        return current.strip() if isinstance(current, str) else ""

    credit = value("credit-name")
    combined = " ".join(part for part in (value("given-names"), value("family-name")) if part)
    return credit or combined


def _orcid_work_matches(
    payload: object, targets: Dict[str, JsonObject]
) -> List[JsonObject]:
    if not isinstance(payload, dict) or not isinstance(payload.get("group"), list):
        raise ValueError("ORCID works response is malformed")
    result = []
    seen = set()
    for group in payload["group"]:
        if not isinstance(group, dict):
            continue
        external_ids = group.get("external-ids")
        identifiers = (
            external_ids.get("external-id")
            if isinstance(external_ids, dict)
            else []
        )
        dois = set()
        if isinstance(identifiers, list):
            for identifier in identifiers:
                if (
                    isinstance(identifier, dict)
                    and str(identifier.get("external-id-type") or "").casefold()
                    == "doi"
                    and isinstance(identifier.get("external-id-value"), str)
                ):
                    dois.add(_normalize_doi(identifier["external-id-value"]))
        matching_targets = [
            target
            for target in targets.values()
            if _normalize_doi(target["doi"]) in dois
        ]
        if not matching_targets:
            continue
        summaries = group.get("work-summary")
        if not isinstance(summaries, list):
            raise ValueError("ORCID exact DOI group has no work summary")
        for target in matching_targets:
            compatible = []
            for summary in summaries:
                if not isinstance(summary, dict):
                    continue
                title = summary.get("title")
                title_value = (
                    title.get("title", {}).get("value")
                    if isinstance(title, dict)
                    else None
                )
                if not isinstance(title_value, str):
                    continue
                if _normalize_title(title_value) == _normalize_title(target["title"]):
                    compatible.append((summary, title_value))
            if not compatible:
                raise ValueError("ORCID exact DOI title identity conflict")
            summary, title_value = compatible[0]
            key = (target["target_id"], _normalize_doi(target["doi"]))
            if key in seen:
                continue
            seen.add(key)
            url = summary.get("url")
            result.append(
                {
                    "target_id": target["target_id"],
                    "doi": _normalize_doi(target["doi"]),
                    "title": title_value,
                    "url": (
                        _canonical_url(url["value"])
                        if isinstance(url, dict)
                        and isinstance(url.get("value"), str)
                        else None
                    ),
                    "visibility": summary.get("visibility"),
                }
            )
    return result


def _orcid_affiliations(payload: object) -> List[JsonObject]:
    if not isinstance(payload, dict) or not isinstance(
        payload.get("affiliation-group"), list
    ):
        raise ValueError("ORCID employments response is malformed")
    result = []
    for group in payload["affiliation-group"]:
        if not isinstance(group, dict):
            continue
        summaries = group.get("summaries")
        if not isinstance(summaries, list):
            continue
        for row in summaries:
            employment = (
                row.get("employment-summary") if isinstance(row, dict) else None
            )
            if not isinstance(employment, dict):
                continue
            organization = employment.get("organization")
            name = organization.get("name") if isinstance(organization, dict) else None
            if not isinstance(name, str) or not name.strip():
                continue
            result.append(
                {
                    "organization": name.strip(),
                    "department": employment.get("department-name"),
                    "role": employment.get("role-title"),
                    "start_date": employment.get("start-date"),
                    "end_date": employment.get("end-date"),
                    "visibility": employment.get("visibility"),
                }
            )
    return result


def _meta_values(parser: _RepositoryHtmlParser, *names: str) -> List[str]:
    result = []
    for name in names:
        result.extend(parser.meta.get(name.casefold(), []))
    return [value for value in result if value]


def _repository_detail(
    request: JsonObject, envelope: JsonObject
) -> JsonObject:
    parser = _parse_html(envelope.get("payload"))
    titles = _meta_values(parser, "DC.title", "citation_title", "og:title")
    if not titles:
        raise ValueError("repository detail has no title metadata")
    compatible_titles = [
        title
        for title in titles
        if _normalize_title(title) == _normalize_title(request["requested_title"])
    ]
    if not compatible_titles:
        raise ValueError("repository detail title identity conflict")
    identifiers = _meta_values(parser, "DC.identifier", "citation_doi")
    doi_values = []
    for value in identifiers:
        candidate = value.strip()
        if candidate.lower().startswith(("doi:", "http://doi.org/", "https://doi.org/")):
            doi_values.append(_normalize_doi(candidate))
        elif re.match(r"^10\.\d{4,9}/\S+$", candidate, flags=re.IGNORECASE):
            doi_values.append(_normalize_doi(candidate))
    requested_doi = _normalize_doi(request["requested_doi"])
    if requested_doi not in doi_values:
        raise ValueError("repository detail DOI identity conflict")

    detail_url = _canonical_url(envelope["request_url"])
    repository_host = _required_text(
        request.get("repository_host"), "repository_host"
    ).lower()
    if urllib.parse.urlsplit(detail_url).hostname != repository_host:
        raise ValueError("repository detail left the verified repository host")

    file_urls = []
    for value in _meta_values(parser, "citation_pdf_url"):
        file_urls.append(_canonical_url(urllib.parse.urljoin(detail_url, value)))
    for link in parser.links:
        href = link.get("href")
        if not isinstance(href, str):
            continue
        href_path = urllib.parse.urlsplit(href).path.casefold()
        if "/bitstream/" not in href_path and "/bitstreams/" not in href_path:
            continue
        absolute = _canonical_url(urllib.parse.urljoin(detail_url, href))
        file_urls.append(absolute)
    file_urls = sorted(set(file_urls))
    for url in file_urls:
        if urllib.parse.urlsplit(url).hostname != repository_host:
            raise ValueError("repository file left the verified repository host")

    abstracts = _meta_values(
        parser, "DCTERMS.abstract", "DC.description", "citation_abstract"
    )
    attachments = [
        {
            "target_id": request["target_id"],
            "repository_id": request["repository_id"],
            "record_url": detail_url,
            "url": url,
            "candidate_kind": "public_repository_file",
            "access_basis": "public file link on exact official repository record",
            "version": "repository-deposited-version-unverified",
        }
        for url in file_urls
    ]
    return {
        "target_id": request["target_id"],
        "institution_name": request["institution_name"],
        "repository_id": request["repository_id"],
        "title": compatible_titles[0],
        "doi": requested_doi,
        "record_url": detail_url,
        "metadata_abstract_sha256": sorted(
            {
                hashlib.sha256(value.encode("utf-8")).hexdigest()
                for value in abstracts
            }
        ),
        "metadata_abstract_admitted_as_field_evidence": False,
        "lawful_attachment_candidates": attachments,
    }


def compile_author_institution_report(
    manifest: JsonObject,
    raw_results: JsonObject,
    manifest_binding: JsonObject,
) -> JsonObject:
    """Normalize public route responses with independent confidence states."""

    if (
        manifest.get("contract") != AUTHOR_INSTITUTION_CONTRACT
        or manifest.get("routing", {}).get("next_internal_route")
        != "execute-author-institution-public-routes"
        or manifest.get("scientific_field_evidence_admitted") is not False
    ):
        raise ValueError("accepted author/institution route manifest is required")
    responses = raw_results.get("responses")
    details = raw_results.get("details")
    if not isinstance(responses, dict) or not isinstance(details, dict):
        raise ValueError("raw route responses are malformed")
    requests = manifest.get("author_requests", []) + manifest.get(
        "institution_requests", []
    )
    request_index = {row["request_id"]: row for row in requests}
    if set(responses) != set(request_index):
        raise ValueError("raw route responses do not cover the manifest")
    institution_request_ids = {
        row["request_id"] for row in manifest.get("institution_requests", [])
    }
    if set(details) != institution_request_ids:
        raise ValueError("repository detail responses do not cover search routes")
    targets = {row["target_id"]: row for row in manifest["target_index"]}

    failed = []
    raw_refs = []
    for request_id, envelope in responses.items():
        raw_refs.append(
            {
                "request_id": request_id,
                "kind": "primary",
                "path": f"raw/{_raw_primary_path(request_id)}",
                "sha256": hashlib.sha256(_json_bytes(envelope)).hexdigest(),
            }
        )
        if _envelope_failed(envelope):
            failed.append({"request_id": request_id, "kind": "primary"})

    author_results = []
    work_matches = []
    identity_confirmed = 0
    for author in manifest["author_targets"]:
        section_envelopes = {
            section: responses[f"orcid:{author['orcid']}:{section}"]
            for section in ORCID_SECTIONS
        }
        person = section_envelopes["person"]
        if _envelope_failed(person):
            public_name = None
            identity_confidence = "route_failed"
        else:
            public_name = _orcid_public_name(person.get("payload"))
            accepted_names = {
                _normalize_name(value) for value in author["expected_names"]
            }
            if _normalize_name(public_name) not in accepted_names:
                raise ValueError("ORCID public name conflicts with exact anchor")
            identity_confidence = "exact_name_match"
            identity_confirmed += 1
        works = section_envelopes["works"]
        if _envelope_failed(works):
            matches = []
            works_status = "route_failed"
        else:
            bound_targets = {
                target_id: targets[target_id] for target_id in author["target_ids"]
            }
            matches = _orcid_work_matches(works.get("payload"), bound_targets)
            works_status = "target_work_matches" if matches else "no_target_work_match"
        work_matches.extend(matches)
        employments = section_envelopes["employments"]
        if _envelope_failed(employments):
            affiliations = []
            affiliation_confidence = "route_failed"
        else:
            affiliations = _orcid_affiliations(employments.get("payload"))
            affiliation_confidence = (
                "public_current_or_undated_not_target_period"
                if affiliations
                else "no_public_employment_support"
            )
        author_results.append(
            {
                "orcid": author["orcid"],
                "target_ids": author["target_ids"],
                "expected_names": author["expected_names"],
                "public_name": public_name,
                "identity_confidence": identity_confidence,
                "works_status": works_status,
                "target_work_matches": matches,
                "affiliation_confidence": affiliation_confidence,
                "public_affiliations": affiliations,
            }
        )

    institution_results = []
    exact_records = []
    attachments = []
    institution_no_hits = 0
    detail_count = 0
    for request in manifest["institution_requests"]:
        request_id = request["request_id"]
        envelope = responses[request_id]
        detail_envelopes = details[request_id]
        if not isinstance(detail_envelopes, list):
            raise ValueError("repository detail response list is malformed")
        raw_refs.append(
            {
                "request_id": request_id,
                "kind": "detail_bundle",
                "path": f"raw/{_raw_detail_path(request_id)}",
                "sha256": hashlib.sha256(
                    _json_bytes(detail_envelopes)
                ).hexdigest(),
            }
        )
        if _envelope_failed(envelope):
            institution_results.append(
                {
                    "request_id": request_id,
                    "target_id": request["target_id"],
                    "institution_name": request["institution_name"],
                    "repository_id": request["repository_id"],
                    "status": "route_failed",
                    "no_hit": False,
                    "exact_repository_records": [],
                }
            )
            if detail_envelopes:
                raise ValueError("failed repository search cannot have detail responses")
            continue
        candidates = extract_exact_repository_detail_candidates(
            request, envelope.get("payload")
        )
        if len(detail_envelopes) != len(candidates):
            raise ValueError("repository detail responses do not match exact candidates")
        route_records = []
        for index, (candidate, detail_envelope) in enumerate(
            zip(candidates, detail_envelopes), start=1
        ):
            detail_count += 1
            if _envelope_failed(detail_envelope):
                failed.append(
                    {
                        "request_id": request_id,
                        "kind": "detail",
                        "detail_index": index,
                    }
                )
                continue
            if _canonical_url(detail_envelope.get("request_url")) != candidate["url"]:
                raise ValueError("repository detail response URL identity conflict")
            record = _repository_detail(request, detail_envelope)
            route_records.append(record)
            exact_records.append(record)
            attachments.extend(record["lawful_attachment_candidates"])
        if route_records:
            status = (
                "lawful_attachment_candidates_found"
                if any(row["lawful_attachment_candidates"] for row in route_records)
                else "exact_repository_records_found_no_file"
            )
            no_hit = False
        elif candidates:
            status = "repository_detail_route_degraded"
            no_hit = False
        else:
            status = "no_exact_repository_record"
            no_hit = True
            institution_no_hits += 1
        institution_results.append(
            {
                "request_id": request_id,
                "target_id": request["target_id"],
                "institution_name": request["institution_name"],
                "repository_id": request["repository_id"],
                "status": status,
                "no_hit": no_hit,
                "exact_repository_records": route_records,
            }
        )

    location_rows = [
        {
            "target_id": row["target_id"],
            "institution_name": row["institution_name"],
            "repository_id": row["repository_id"],
            "title": row["title"],
            "doi": row["doi"],
            "url": row["record_url"],
            "candidate_kind": "exact_public_repository_metadata_record",
            "attachment_available": bool(row["lawful_attachment_candidates"]),
        }
        for row in exact_records
    ]
    if attachments:
        next_route = "ingest-lawful-attachment-candidates"
    elif exact_records:
        next_route = "expand-from-exact-repository-records"
    elif failed:
        next_route = "retry-degraded-author-institution-routes"
    else:
        next_route = "expand-another-public-source-class"
    status = (
        "author_institution_routes_completed_degraded"
        if failed
        else (
            "author_institution_routes_completed_attachments"
            if attachments
            else (
                "author_institution_routes_completed_exact_records"
                if exact_records
                else "author_institution_routes_completed_no_record"
            )
        )
    )
    primary_count = len(requests)
    return {
        "schema_version": 1,
        "contract": AUTHOR_INSTITUTION_CONTRACT,
        "scope_id": manifest.get("scope_id"),
        "revision": manifest.get("revision"),
        "status": status,
        "route_manifest": _artifact(manifest_binding, "manifest_binding"),
        "author_target_count": manifest["author_target_count"],
        "institution_target_count": manifest["institution_target_count"],
        "resolved_institution_route_count": sum(
            row["resolution_status"] == "verified_public_repository"
            for row in manifest["institution_targets"]
        ),
        "unresolved_institution_route_count": manifest[
            "unresolved_institution_route_count"
        ],
        "primary_request_count": primary_count,
        "detail_request_count": detail_count,
        "executed_request_count": primary_count + detail_count,
        "successful_request_count": primary_count + detail_count - len(failed),
        "failed_request_count": len(failed),
        "institution_search_no_hit_count": institution_no_hits,
        "author_identity_confirmed_count": identity_confirmed,
        "orcid_target_work_match_count": len(work_matches),
        "exact_repository_record_count": len(exact_records),
        "public_repository_location_count": len(location_rows),
        "lawful_attachment_candidate_count": len(attachments),
        "author_results": author_results,
        "orcid_target_work_matches": work_matches,
        "institution_results": institution_results,
        "institution_route_registry": manifest["institution_targets"],
        "exact_repository_records": exact_records,
        "public_repository_locations": location_rows,
        "lawful_attachment_candidates": attachments,
        "raw_response_refs": raw_refs,
        "routing": {
            "retry_requests": failed,
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
        "next_action": {
            "ingest-lawful-attachment-candidates": (
                "Validate candidate bytes, hashes, media types, versions, and access bases before parsing."
            ),
            "expand-from-exact-repository-records": (
                "Inspect exact repository records for supported exports, OAI identifiers, and public version routes without treating metadata as full text."
            ),
            "retry-degraded-author-institution-routes": (
                "Retry only the bounded degraded public routes while continuing other lawful source classes."
            ),
            "expand-another-public-source-class": (
                "Add another lawful public source class while retaining all no-hit and unresolved-route evidence."
            ),
        }[next_route],
    }


def execute_author_institution_routes(
    manifest: JsonObject,
    raw_root: Path,
    manifest_binding: JsonObject,
    *,
    attempts: int = 3,
    timeout: int = 20,
) -> JsonObject:
    """Execute every public route and follow exact repository results once."""

    if manifest.get("contract") != AUTHOR_INSTITUTION_CONTRACT:
        raise ValueError("author/institution route manifest is required")
    raw_root = raw_root.resolve()
    responses = {}
    for request in manifest.get("author_requests", []) + manifest.get(
        "institution_requests", []
    ):
        envelope = fetch_public_request(
            request, attempts=attempts, timeout=timeout
        )
        responses[request["request_id"]] = envelope
        path = raw_root / _raw_primary_path(request["request_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_json_bytes(envelope))

    details = {}
    for request in manifest.get("institution_requests", []):
        request_id = request["request_id"]
        search_envelope = responses[request_id]
        detail_envelopes = []
        if not _envelope_failed(search_envelope):
            candidates = extract_exact_repository_detail_candidates(
                request, search_envelope.get("payload")
            )
            if len(candidates) > 5:
                raise ValueError("repository exact-title search returned too many details")
            for index, candidate in enumerate(candidates, start=1):
                detail_request = {
                    **request,
                    "request_id": f"{request_id}:detail-{index}",
                    "route_id": "institution-public-repository-detail",
                    "request_url": candidate["url"],
                    "response_kind": "html",
                    "search_result_title": candidate["search_result_title"],
                }
                detail_envelopes.append(
                    fetch_public_request(
                        detail_request, attempts=attempts, timeout=timeout
                    )
                )
        details[request_id] = detail_envelopes
        path = raw_root / _raw_detail_path(request_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_json_bytes(detail_envelopes))

    return compile_author_institution_report(
        manifest,
        {"responses": responses, "details": details},
        manifest_binding,
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
    report = execute_author_institution_routes(
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
