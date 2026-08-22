"""Probe exact KIST repository records for exports, OAI, and public files."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence


JsonObject = Dict[str, object]

RECORD_EXPORT_CONTRACT = (
    "lca_research_kernel.exact_repository_record_export_probe/v1"
)
AUTHOR_INSTITUTION_CONTRACT = (
    "lca_research_kernel.author_institution_public_routes/v1"
)
KIST_REPOSITORY_ID = "kist-library-institutional-repository"
KIST_HOST = "pubs.kist.re.kr"
KIST_OAI_BASE = "https://pubs.kist.re.kr/oai/request"
ROUTE_KEYS = ("full", "dc", "ris", "excel", "oai_handle", "oai_item")


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


def _normalize_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", _required_text(value, "title"))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"(?<=[A-Za-z])\((\d+)\)", r"\1", text)
    return " ".join(re.findall(r"[\w]+", text.casefold()))


def _normalize_doi(value: object) -> str:
    text = _required_text(value, "DOI").casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.strip()


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


def _handle_from_url(value: object) -> str:
    url = _canonical_url(value)
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname != KIST_HOST:
        raise ValueError("exact record is not on the reviewed KIST repository host")
    match = re.fullmatch(r"/handle/(\d+)/(\d+)", parsed.path.rstrip("/"))
    if not match or parsed.query:
        raise ValueError("KIST record URL must be an exact two-part handle")
    return f"{match.group(1)}/{match.group(2)}"


def compile_record_export_manifest(
    author_institution_report: JsonObject,
    author_institution_binding: JsonObject,
) -> JsonObject:
    """Compile bounded record/export requests from accepted exact KIST records."""

    if (
        author_institution_report.get("contract") != AUTHOR_INSTITUTION_CONTRACT
        or author_institution_report.get("routing", {}).get("next_internal_route")
        != "expand-from-exact-repository-records"
        or author_institution_report.get("scientific_field_evidence_admitted")
        is not False
        or author_institution_report.get("scientific_comparison_ready") is not False
        or author_institution_report.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted author/institution route is required")
    records = author_institution_report.get("exact_repository_records")
    if not isinstance(records, list) or not records:
        raise ValueError("accepted exact repository records are required")

    targets = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("exact repository record must be an object")
        if record.get("repository_id") != KIST_REPOSITORY_ID:
            raise ValueError("this vertical probe accepts exact KIST records only")
        target_id = _required_text(record.get("target_id"), "target_id")
        if target_id in seen:
            raise ValueError(f"duplicate exact record target: {target_id}")
        seen.add(target_id)
        record_url = _canonical_url(record.get("record_url"))
        handle = _handle_from_url(record_url)
        if record.get("lawful_attachment_candidates") not in (None, []):
            raise ValueError("record-export route requires records without attachments")
        targets.append(
            {
                "target_id": target_id,
                "doi": _normalize_doi(record.get("doi")),
                "title": _required_text(record.get("title"), f"{target_id}.title"),
                "institution_name": _required_text(
                    record.get("institution_name"), f"{target_id}.institution_name"
                ),
                "repository_id": KIST_REPOSITORY_ID,
                "repository_host": KIST_HOST,
                "record_url": record_url,
                "handle": handle,
            }
        )
    targets.sort(key=lambda row: row["target_id"])
    retries = author_institution_report.get("routing", {}).get(
        "retry_requests", []
    )
    if not isinstance(retries, list) or not all(isinstance(row, dict) for row in retries):
        raise ValueError("retained retry requests are malformed")
    binding = _artifact(author_institution_binding, "author_institution_binding")
    return {
        "schema_version": 1,
        "contract": RECORD_EXPORT_CONTRACT,
        "scope_id": _required_text(author_institution_report.get("scope_id"), "scope_id"),
        "revision": _required_text(author_institution_report.get("revision"), "revision"),
        "status": "exact_repository_record_export_probe_compiled",
        "author_institution_report": binding,
        "record_target_count": len(targets),
        "planned_request_count": len(targets) * len(ROUTE_KEYS),
        "record_targets": targets,
        "retained_retry_requests": retries,
        "routing": {
            "human_routes_untouched": author_institution_report.get(
                "routing", {}
            ).get("human_routes_untouched", []),
            "next_internal_route": "execute-exact-record-export-probe",
            "pause_internal_work": False,
        },
        "scientific_field_evidence_admitted": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
    }


def _raw_path(target_id: str, route_key: str) -> str:
    if route_key not in ROUTE_KEYS:
        raise ValueError(f"unknown record probe route: {route_key}")
    safe_target = urllib.parse.quote(target_id, safe="")
    return f"records/{safe_target}/{route_key.replace('_', '-')}.json"


def expected_probe_output_paths(manifest: JsonObject) -> List[str]:
    targets = manifest.get("record_targets")
    if not isinstance(targets, list):
        raise ValueError("record targets are malformed")
    return [
        _raw_path(_required_text(target.get("target_id"), "target_id"), route)
        for target in targets
        if isinstance(target, dict)
        for route in ROUTE_KEYS
    ]


def _request(
    target: JsonObject,
    route_id: str,
    request_url: str,
    response_kind: str,
    *,
    oai_identifier: Optional[str] = None,
) -> JsonObject:
    value = {
        "target_id": target["target_id"],
        "route_id": route_id,
        "requested_doi": target["doi"],
        "requested_title": target["title"],
        "record_url": target["record_url"],
        "request_url": _canonical_url(request_url),
        "response_kind": response_kind,
    }
    if oai_identifier is not None:
        value["oai_identifier"] = oai_identifier
    return value


def build_record_requests(
    target: JsonObject, *, item_id: Optional[str] = None
) -> Dict[str, JsonObject]:
    """Build stable full/OAI requests and item-bound export requests."""

    handle = _required_text(target.get("handle"), "handle")
    base = _canonical_url(target.get("record_url"))
    full_url = urllib.parse.urlunsplit(
        (*urllib.parse.urlsplit(base)[:3], "mode=full", "")
    )
    handle_identifier = f"oai:{KIST_HOST}:{handle}"

    def oai_url(identifier: str) -> str:
        return f"{KIST_OAI_BASE}?{urllib.parse.urlencode({'verb': 'GetRecord', 'metadataPrefix': 'oai_dc', 'identifier': identifier})}"

    result = {
        "full": _request(target, "kist-full-record", full_url, "text"),
        "oai_handle": _request(
            target,
            "kist-oai-handle-identifier",
            oai_url(handle_identifier),
            "text",
            oai_identifier=handle_identifier,
        ),
    }
    if item_id is None:
        return result
    if not re.fullmatch(r"\d+", item_id):
        raise ValueError("KIST export item_id must be numeric")
    for kind in ("dc", "ris", "excel"):
        endpoint = {"dc": "export-dc", "ris": "export-ris", "excel": "export-excel"}[kind]
        response_kind = "binary" if kind == "excel" else "text"
        result[kind] = _request(
            target,
            f"kist-{kind}-metadata-export",
            f"https://{KIST_HOST}/{endpoint}?item_id={item_id}",
            response_kind,
        )
    item_identifier = f"oai:{KIST_HOST}:201004/{item_id}"
    result["oai_item"] = _request(
        target,
        "kist-oai-internal-item-identifier",
        oai_url(item_identifier),
        "text",
        oai_identifier=item_identifier,
    )
    return {key: result[key] for key in ROUTE_KEYS}


class _RecordHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: Dict[str, List[str]] = {}
        self.links: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        values = {str(key).casefold(): value for key, value in attrs}
        if tag.casefold() == "meta":
            name = values.get("name") or values.get("property")
            content = values.get("content")
            if isinstance(name, str) and isinstance(content, str):
                self.meta.setdefault(name.casefold(), []).append(content.strip())
        elif tag.casefold() == "a" and isinstance(values.get("href"), str):
            self.links.append(values["href"].strip())


def analyze_full_record(target: JsonObject, html_text: str) -> JsonObject:
    """Verify one exact record and separate metadata exports from bitstreams."""

    if not isinstance(html_text, str):
        raise ValueError("full record payload must be text")
    parser = _RecordHtmlParser()
    parser.feed(html_text)
    parser.close()
    titles = parser.meta.get("dc.title", []) + parser.meta.get("citation_title", [])
    expected_title = _normalize_title(target.get("title"))
    if not any(_normalize_title(value) == expected_title for value in titles):
        raise ValueError("full record title conflicts with accepted exact record")
    identifiers = parser.meta.get("dc.identifier", []) + parser.meta.get(
        "citation_doi", []
    )
    expected_doi = _normalize_doi(target.get("doi"))
    normalized_identifiers = []
    for value in identifiers:
        try:
            normalized_identifiers.append(_normalize_doi(value))
        except ValueError:
            continue
    if expected_doi not in normalized_identifiers:
        raise ValueError("full record DOI conflicts with accepted exact record")

    record_url = _canonical_url(target.get("record_url"))
    export_urls: Dict[str, str] = {}
    item_ids = set()
    bitstreams = []
    seen_files = set()
    for href in parser.links:
        if not href or href.casefold().startswith(("javascript:", "mailto:")):
            continue
        absolute = _canonical_url(urllib.parse.urljoin(record_url, href))
        parsed = urllib.parse.urlsplit(absolute)
        route_match = re.fullmatch(r"/export-(dc|ris|excel)", parsed.path)
        if route_match:
            if parsed.hostname != KIST_HOST:
                raise ValueError("metadata export left the reviewed repository host")
            query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
            ids = query.get("item_id", [])
            if len(ids) != 1 or not re.fullmatch(r"\d+", ids[0]):
                raise ValueError("metadata export has invalid item_id")
            kind = route_match.group(1)
            if kind in export_urls and export_urls[kind] != absolute:
                raise ValueError(f"conflicting {kind} metadata export links")
            export_urls[kind] = absolute
            item_ids.add(ids[0])
            continue
        is_file = "/bitstream/" in parsed.path.casefold() or parsed.path.casefold().endswith(
            ".pdf"
        )
        if not is_file or parsed.hostname != KIST_HOST or absolute in seen_files:
            continue
        seen_files.add(absolute)
        bitstreams.append(
            {
                "target_id": target["target_id"],
                "doi": target["doi"],
                "title": target["title"],
                "record_url": record_url,
                "url": absolute,
                "candidate_kind": "public_exact_record_bitstream",
                "access_basis": "public_link_on_exact_institutional_repository_record",
                "attachment_bytes_validated": False,
            }
        )
    if set(export_urls) != {"dc", "ris", "excel"}:
        raise ValueError("exact KIST record must expose DC, RIS, and Excel exports")
    if len(item_ids) != 1:
        raise ValueError("KIST metadata exports do not share one item_id")
    return {
        "status": "record_html_exact",
        "item_id": next(iter(item_ids)),
        "export_urls": export_urls,
        "public_bitstream_candidates": bitstreams,
    }


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def parse_oai_response(xml_text: str) -> JsonObject:
    """Parse OAI-PMH semantic success or error independently of HTTP status."""

    try:
        root = ET.fromstring(xml_text)
    except (TypeError, ET.ParseError) as error:
        raise ValueError("OAI response is invalid XML") from error
    errors = [element for element in root.iter() if _local_name(element) == "error"]
    if errors:
        first = errors[0]
        code = _required_text(first.attrib.get("code"), "OAI error code")
        return {
            "status": "oai_semantic_error",
            "record_available": False,
            "error_code": code,
            "error_message": " ".join("".join(first.itertext()).split()),
        }
    records = [element for element in root.iter() if _local_name(element) == "record"]
    if len(records) != 1:
        raise ValueError("OAI GetRecord response has neither one record nor an error")
    return {
        "status": "oai_record_success",
        "record_available": True,
        "error_code": None,
        "error_message": None,
    }


def _parse_dc_export(xml_text: str, target: JsonObject) -> JsonObject:
    try:
        root = ET.fromstring(xml_text)
    except (TypeError, ET.ParseError) as error:
        raise ValueError("DC export is invalid XML") from error
    titles = []
    identifiers = []
    for element in root.iter():
        if _local_name(element) != "dcvalue":
            continue
        value = " ".join("".join(element.itertext()).split())
        if element.attrib.get("element") == "title":
            titles.append(value)
        if element.attrib.get("element") == "identifier":
            identifiers.append(value)
    if not any(_normalize_title(value) == _normalize_title(target["title"]) for value in titles):
        raise ValueError("DC export title conflicts with exact record")
    if not any(
        _normalize_doi(value) == _normalize_doi(target["doi"])
        for value in identifiers
        if isinstance(value, str) and "10." in value
    ):
        raise ValueError("DC export DOI conflicts with exact record")
    return {"status": "metadata_export_exact", "format": "dc-xml"}


def _parse_ris_export(text: str, target: JsonObject) -> JsonObject:
    fields: Dict[str, List[str]] = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Z0-9]{2})\s+-\s*(.*)$", line)
        if match:
            fields.setdefault(match.group(1), []).append(match.group(2).strip())
    if not any(_normalize_title(value) == _normalize_title(target["title"]) for value in fields.get("TI", [])):
        raise ValueError("RIS export title conflicts with exact record")
    if not any(_normalize_doi(value) == _normalize_doi(target["doi"]) for value in fields.get("DO", [])):
        raise ValueError("RIS export DOI conflicts with exact record")
    return {"status": "metadata_export_exact", "format": "ris"}


def _payload_text(envelope: JsonObject, label: str) -> str:
    if envelope.get("payload_encoding") != "utf-8":
        raise ValueError(f"{label} must use UTF-8 payload encoding")
    return _required_text(envelope.get("payload"), f"{label}.payload")


def _payload_bytes(envelope: JsonObject, label: str) -> bytes:
    if envelope.get("payload_encoding") != "base64":
        raise ValueError(f"{label} must use base64 payload encoding")
    try:
        return base64.b64decode(
            _required_text(envelope.get("payload"), f"{label}.payload"),
            validate=True,
        )
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError(f"{label} has invalid base64 payload") from error


def _failed(envelope: object) -> bool:
    return not isinstance(envelope, dict) or envelope.get("error") is not None


def compile_record_export_report(
    manifest: JsonObject,
    raw_results: JsonObject,
    manifest_binding: JsonObject,
) -> JsonObject:
    """Compile record, metadata, OAI, and bitstream states from raw envelopes."""

    if manifest.get("contract") != RECORD_EXPORT_CONTRACT:
        raise ValueError("record export probe manifest is required")
    records = raw_results.get("records")
    if not isinstance(records, dict):
        raise ValueError("raw record probe results are missing")
    binding = _artifact(manifest_binding, "manifest_binding")
    record_results = []
    raw_refs = []
    failed_requests = []
    all_attachments = []
    exact_records = 0
    metadata_exports = 0
    metadata_only = 0
    oai_successes = 0
    oai_errors = 0
    executed = 0

    for target in manifest.get("record_targets", []):
        if not isinstance(target, dict):
            raise ValueError("record target is malformed")
        target_id = target["target_id"]
        route_envelopes = records.get(target_id)
        if not isinstance(route_envelopes, dict) or set(route_envelopes) != set(
            ROUTE_KEYS
        ):
            raise ValueError(f"raw routes do not match manifest for {target_id}")
        for route_key in ROUTE_KEYS:
            envelope = route_envelopes[route_key]
            if not isinstance(envelope, dict):
                raise ValueError(f"{target_id}.{route_key} envelope is malformed")
            executed += 1
            raw_refs.append(
                {
                    "target_id": target_id,
                    "route_key": route_key,
                    "path": f"raw/{_raw_path(target_id, route_key)}",
                    "sha256": hashlib.sha256(_json_bytes(envelope)).hexdigest(),
                }
            )
            if _failed(envelope):
                failed_requests.append(
                    {"target_id": target_id, "route_key": route_key}
                )

        full = route_envelopes["full"]
        analysis = None
        target_exports = []
        target_oai = []
        attachments = []
        if not _failed(full):
            analysis = analyze_full_record(target, _payload_text(full, "full"))
            exact_records += 1
            attachments = analysis["public_bitstream_candidates"]
            all_attachments.extend(attachments)

        if analysis is not None:
            for route_key in ("dc", "ris", "excel"):
                envelope = route_envelopes[route_key]
                if _failed(envelope):
                    continue
                if _canonical_url(envelope.get("request_url")) != analysis["export_urls"][route_key]:
                    raise ValueError(f"{route_key} response left discovered exact export")
                if route_key == "dc":
                    result = _parse_dc_export(_payload_text(envelope, "dc"), target)
                elif route_key == "ris":
                    result = _parse_ris_export(_payload_text(envelope, "ris"), target)
                else:
                    body = _payload_bytes(envelope, "excel")
                    if not body.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
                        raise ValueError("Excel export is not a legacy metadata workbook")
                    result = {
                        "status": "metadata_export_exact",
                        "format": "xls-cdfv2",
                    }
                metadata_exports += 1
                target_exports.append(
                    {
                        **result,
                        "route_key": route_key,
                        "response_sha256": envelope.get("response_sha256"),
                        "response_bytes": envelope.get("response_bytes"),
                    }
                )

        for route_key in ("oai_handle", "oai_item"):
            envelope = route_envelopes[route_key]
            if _failed(envelope):
                continue
            result = parse_oai_response(_payload_text(envelope, route_key))
            if result["status"] == "oai_record_success":
                oai_successes += 1
            else:
                oai_errors += 1
            target_oai.append(
                {
                    **result,
                    "route_key": route_key,
                    "oai_identifier": envelope.get("oai_identifier"),
                }
            )
        is_metadata_only = (
            analysis is not None
            and len(target_exports) == 3
            and not attachments
        )
        if is_metadata_only:
            metadata_only += 1
        record_results.append(
            {
                "target_id": target_id,
                "doi": target["doi"],
                "title": target["title"],
                "record_url": target["record_url"],
                "status": (
                    "public_bitstream_candidates_found"
                    if attachments
                    else (
                        "exact_record_metadata_only"
                        if is_metadata_only
                        else "exact_record_probe_degraded"
                    )
                ),
                "item_id": analysis.get("item_id") if analysis else None,
                "metadata_exports": target_exports,
                "oai_results": target_oai,
                "public_bitstream_candidates": attachments,
                "scientific_field_evidence_admitted": False,
            }
        )

    if all_attachments:
        next_route = "ingest-lawful-attachment-candidates"
        status = "record_export_probe_completed_attachments"
    elif failed_requests:
        next_route = "retry-degraded-exact-record-probes"
        status = "record_export_probe_completed_degraded"
    else:
        next_route = "expand-korean-national-public-sources"
        status = "record_export_probe_completed_metadata_only"
    next_actions = {
        "ingest-lawful-attachment-candidates": (
            "Validate public bitstream bytes, hashes, media types, versions, and access bases before parsing."
        ),
        "retry-degraded-exact-record-probes": (
            "Retry only the failed exact-record requests, preserving semantic OAI errors and all successful exports."
        ),
        "expand-korean-national-public-sources": (
            "Search bounded Korean national scholarly sources by exact DOI/title while preserving the metadata-only KIST and degraded SNU results."
        ),
    }
    return {
        "schema_version": 1,
        "contract": RECORD_EXPORT_CONTRACT,
        "scope_id": manifest.get("scope_id"),
        "revision": manifest.get("revision"),
        "status": status,
        "probe_manifest": binding,
        "record_target_count": manifest["record_target_count"],
        "planned_request_count": manifest["planned_request_count"],
        "executed_request_count": executed,
        "successful_transport_count": executed - len(failed_requests),
        "failed_request_count": len(failed_requests),
        "exact_record_count": exact_records,
        "metadata_export_count": metadata_exports,
        "metadata_only_record_count": metadata_only,
        "oai_record_success_count": oai_successes,
        "oai_semantic_error_count": oai_errors,
        "public_bitstream_candidate_count": len(all_attachments),
        "lawful_attachment_candidate_count": len(all_attachments),
        "retained_retry_count": len(manifest.get("retained_retry_requests", [])),
        "record_results": record_results,
        "lawful_attachment_candidates": all_attachments,
        "raw_response_refs": raw_refs,
        "routing": {
            "failed_requests": failed_requests,
            "retained_retry_requests": manifest.get("retained_retry_requests", []),
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
    """Fetch one bounded public text or binary record route."""

    if attempts < 1 or timeout < 1:
        raise ValueError("attempts and timeout must be positive")
    open_call = open_url or urllib.request.urlopen
    request_url = _canonical_url(request.get("request_url"))
    response_kind = _required_text(request.get("response_kind"), "response_kind")
    if response_kind not in {"text", "binary"}:
        raise ValueError("response_kind must be text or binary")
    last_error = None
    last_status = None
    for attempt in range(1, attempts + 1):
        url_request = urllib.request.Request(
            request_url,
            headers={
                "User-Agent": "TianGong-LCA-Research-Agent/1.0 (public exact-record probe)",
                "Accept": (
                    "application/xml,text/plain,text/html;q=0.9,*/*;q=0.1"
                    if response_kind == "text"
                    else "application/vnd.ms-excel,application/octet-stream;q=0.9,*/*;q=0.1"
                ),
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
            if urllib.parse.urlsplit(response_url).hostname != KIST_HOST:
                raise ValueError("record probe redirect left the KIST repository host")
            if response_kind == "text":
                try:
                    payload = body.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ValueError("record probe response is not UTF-8") from error
                encoding = "utf-8"
            else:
                payload = base64.b64encode(body).decode("ascii")
                encoding = "base64"
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
                "payload_encoding": encoding,
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
        "error": last_error or "record probe failed without a response",
        "payload_encoding": None,
        "payload": None,
    }


def _skipped_envelope(target: JsonObject, route_key: str, reason: str) -> JsonObject:
    return {
        "target_id": target["target_id"],
        "route_id": f"kist-{route_key.replace('_', '-')}",
        "requested_doi": target["doi"],
        "requested_title": target["title"],
        "record_url": target["record_url"],
        "request_url": target["record_url"],
        "response_kind": "text",
        "fetched_at": _fetched_at(),
        "http_status": None,
        "attempt_count": 0,
        "content_type": None,
        "response_url": None,
        "response_sha256": None,
        "response_bytes": 0,
        "error": reason,
        "payload_encoding": None,
        "payload": None,
    }


def execute_record_export_probe(
    manifest: JsonObject,
    raw_root: Path,
    manifest_binding: JsonObject,
    *,
    attempts: int = 3,
    timeout: int = 20,
) -> JsonObject:
    """Execute all bounded exact-record routes and persist stable envelopes."""

    if manifest.get("contract") != RECORD_EXPORT_CONTRACT:
        raise ValueError("record export probe manifest is required")
    raw_root = raw_root.resolve()
    records = {}
    for target in manifest.get("record_targets", []):
        initial = build_record_requests(target)
        full = fetch_public_request(
            initial["full"], attempts=attempts, timeout=timeout
        )
        oai_handle = fetch_public_request(
            initial["oai_handle"], attempts=attempts, timeout=timeout
        )
        route_envelopes = {"full": full, "oai_handle": oai_handle}
        if not _failed(full):
            analysis = analyze_full_record(target, _payload_text(full, "full"))
            requests = build_record_requests(target, item_id=analysis["item_id"])
            for route_key in ("dc", "ris", "excel", "oai_item"):
                route_envelopes[route_key] = fetch_public_request(
                    requests[route_key], attempts=attempts, timeout=timeout
                )
        else:
            for route_key in ("dc", "ris", "excel", "oai_item"):
                route_envelopes[route_key] = _skipped_envelope(
                    target, route_key, "full record unavailable; dependent route not executed"
                )
        ordered = {route_key: route_envelopes[route_key] for route_key in ROUTE_KEYS}
        records[target["target_id"]] = ordered
        for route_key, envelope in ordered.items():
            path = raw_root / _raw_path(target["target_id"], route_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_json_bytes(envelope))
    return compile_record_export_report(
        manifest, {"records": records}, manifest_binding
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
    report = execute_record_export_probe(
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
