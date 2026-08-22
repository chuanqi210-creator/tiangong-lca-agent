"""Bounded, credential-free reads from a fixed set of official public hosts."""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Mapping

from .capability_gateway import CapabilityGatewayError
from .official_source_binding import build_official_source_observation


CAPABILITY_ID = "official-public-source-read"
REQUEST_CONTRACT = "official_public_source_request/v1"
ALLOWED_HOSTS = frozenset(
    {
        "eur-lex.europa.eu",
        "taxation-customs.ec.europa.eu",
        "ec.europa.eu",
        "climate.ec.europa.eu",
        "data.europa.eu",
        "publications.europa.eu",
    }
)
ALLOWED_METHODS = frozenset({"GET", "HEAD"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
ALLOWED_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "application/pdf",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/xml",
        "application/xhtml+xml",
        "text/csv",
        "text/html",
        "text/plain",
        "text/xml",
    }
)
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_REDIRECTS = 3
IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
COMPOSITE_KEY_FIELDS = frozenset(
    {
        "country_or_region",
        "cn_code",
        "year",
        "emission_type",
        "production_route",
        "markup_status",
    }
)
EXCEPTION_SCAN_FIELDS = frozenset(
    {"except", "only", "N/A", "exact_cn_exceptions"}
)
TEXT_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
        "text/csv",
        "text/html",
        "text/plain",
        "text/xml",
    }
)
MAX_EXCERPT_BYTES = 16 * 1024
SOURCE_INDEX_CONTRACT = (
    "lca_research_kernel.official_public_source_index/v1"
)
SOURCE_LOCATOR_CONTRACT = "lca_research_kernel.official_source_locator/v1"
DEFAULT_SOURCE_INDEX_PATH = (
    Path(__file__).resolve().parent
    / "contracts"
    / "official-public-source-index.json"
)
SOURCE_INDEX_FIELDS = frozenset(
    {
        "source_id",
        "title",
        "declared_version",
        "url",
        "authority",
        "legal_status",
        "applicable_time",
        "topics",
        "parse_hints",
    }
)
SOURCE_INDEX_OPTIONAL_FIELDS = frozenset({"retrieval_urls"})
GENERIC_PARSE_LOCATIONS = frozenset(
    {
        "body",
        "document body",
        "full document",
        "whole document",
        "official page",
    }
)
PARSE_STOP_WORDS = frozenset(
    {
        "about",
        "and",
        "body",
        "document",
        "for",
        "from",
        "official",
        "page",
        "section",
        "source",
        "the",
        "with",
    }
)
REQUEST_FIELDS = frozenset(
    {
        "contract",
        "question_id",
        "run_id",
        "method",
        "url",
        "parse_location",
        "refresh",
        "extraction",
        "industry_summary",
    }
)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _header(headers: Mapping[str, object], name: str) -> str | None:
    for key, value in headers.items():
        if str(key).lower() == name.lower() and isinstance(value, str):
            return value.strip()
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _VisibleTextParser(HTMLParser):
    """Extract visible HTML text without executing or retaining active content."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag):
        if (
            tag.casefold() in {"script", "style", "noscript", "template"}
            and self._ignored_depth
        ):
            self._ignored_depth -= 1

    def handle_data(self, data):
        if self._ignored_depth == 0 and data.strip():
            self.parts.append(data)


class StdlibHTTPSGetTransport:
    """One-hop stdlib transport; redirect policy remains in the capability."""

    def __init__(self):
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(
        self,
        method: str,
        url: str,
        *,
        timeout_seconds: float,
        max_bytes: int,
        headers: Mapping[str, str],
    ) -> dict:
        request = urllib.request.Request(
            url,
            method=method,
            headers=dict(headers),
        )
        try:
            response = self._opener.open(request, timeout=timeout_seconds)
        except urllib.error.HTTPError as error:
            response = error
        try:
            body = b"" if method == "HEAD" else response.read(max_bytes + 1)
            return {
                "status": int(response.status),
                "headers": dict(response.headers.items()),
                "body": body,
            }
        finally:
            response.close()


class OfficialPublicSourceCapability:
    def __init__(
        self,
        cache_root: Path,
        *,
        transport: object = None,
        wall_clock: Callable[[], str],
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        remaining_seconds: Callable[[], float] = None,
        answer_timing: object = None,
        source_index_path: Path = None,
    ):
        if not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("official source max_bytes is invalid")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("official source timeout is invalid")
        if not callable(wall_clock):
            raise ValueError("official source wall clock is required")
        self.cache_root = Path(cache_root)
        self.transport = transport or StdlibHTTPSGetTransport()
        self.wall_clock = wall_clock
        self.max_bytes = max_bytes
        self.timeout_seconds = float(timeout_seconds)
        self.remaining_seconds = remaining_seconds
        self.answer_timing = answer_timing
        self.source_index_path = Path(
            source_index_path or DEFAULT_SOURCE_INDEX_PATH
        ).resolve()
        self.source_index_sha256 = _sha256_bytes(
            self.source_index_path.read_bytes()
        )
        self.source_index = self._load_source_index(self.source_index_path)

    def describe(self) -> dict:
        return {
            "capability_id": CAPABILITY_ID,
            "authority": "official_public_source",
            "access": "read_only",
            "effect": "external_read",
            "result_status": "sourced_result_not_fact",
            "public_credential_free": True,
            "allowed_methods": sorted(ALLOWED_METHODS),
            "allowed_hosts": sorted(ALLOWED_HOSTS),
            "credentials": "forbidden",
        }

    def context_descriptor(self) -> dict:
        """Return the exact source locator that model context may select from."""

        self._assert_source_index_integrity()
        return {
            "contract": SOURCE_LOCATOR_CONTRACT,
            "source_index_id": self.source_index["index_id"],
            "source_index_sha256": self.source_index_sha256,
            "authority": self.source_index["authority"],
            "selection_policy": (
                "select_exact_url_and_parse_hint_from_this_index_only"
            ),
            "sources": [
                {
                    key: deepcopy(value)
                    for key, value in source.items()
                    if key != "retrieval_urls"
                }
                for source in self.source_index["sources"]
            ],
        }

    def network_host_for(self, payload: Mapping[str, object]) -> str:
        self._assert_source_index_integrity()
        try:
            if not isinstance(payload, Mapping) or not isinstance(
                payload.get("url"), str
            ):
                raise ValueError("official source URL is absent")
            return self._validate_url(payload["url"])
        except ValueError:
            self._fail(
                "official_source_invalid_url",
                "official source URL is not an exact allowed HTTPS source",
                retryable=False,
                next_route="repair-source-locator-selection",
            )
        raise AssertionError("unreachable")

    def execute(
        self,
        payload: Mapping[str, object],
        catalog_payload: Mapping[str, object] = None,
        *,
        execution_identity: Mapping[str, object] = None,
        operation_receipt: Mapping[str, object] = None,
    ) -> dict:
        self._assert_source_index_integrity()
        if catalog_payload is not None:
            payload = catalog_payload
        request = self._validate_request(payload)
        requested_url = request["url"]
        cached = self._read_cache(request)
        if cached is not None and request.get("refresh") is not True:
            return self._cached_result(cached, request)
        initial_read_count = self._read_count(request)
        retrieval_urls = (
            self._retrieval_urls(requested_url)
            if request["method"] == "GET"
            else ()
        )
        initial_url = retrieval_urls[0] if retrieval_urls else requested_url
        response, current_url, read_count = self._fetch_final_response(
            request, initial_url
        )
        primary_status = response["status"]
        retrieval_fallback = None
        retrieval_resolution = None
        if initial_url != requested_url:
            retrieval_resolution = {
                "contract": "official_public_source_retrieval_resolution/v1",
                "selection": "index_qualified_direct_retrieval",
                "canonical_url": requested_url,
                "retrieval_url": initial_url,
            }
        if request["method"] == "GET" and (
            response["status"] != 200 or not response["body"]
        ):
            fallback_urls = (
                (*retrieval_urls[1:], requested_url)
                if retrieval_urls
                else retrieval_urls
            )
            for retrieval_url in fallback_urls:
                response, current_url, read_count = (
                    self._fetch_final_response(request, retrieval_url)
                )
                if response["status"] == 200 and response["body"]:
                    retrieval_fallback = {
                        "used": True,
                        "primary_status": primary_status,
                        "retrieval_url": retrieval_url,
                    }
                    break
        if response["status"] == 429:
            self._fail(
                "official_source_rate_limited",
                "official source remained rate limited after a bounded retry",
                retryable=True,
                next_route="retry-once-or-return-bounded-source-gap",
            )
        if request["method"] == "GET" and response["status"] != 200:
            self._fail(
                "official_source_non_final_status",
                "official source GET did not return a final 200 response",
                retryable=False,
                next_route="select-a-final-official-document",
            )
        if response["status"] < 200 or response["status"] >= 300:
            self._fail(
                "official_source_http_failure",
                "official source returned a non-success status",
                retryable=False,
                next_route="return-bounded-source-gap",
            )
        body = response["body"]
        if request["method"] == "GET" and not body:
            self._fail(
                "official_source_empty_body",
                "official source GET returned no evidence bytes",
                retryable=False,
                next_route="select-an-official-document-with-content",
            )
        if len(body) > self.max_bytes:
            self._fail(
                "official_source_size_exceeded",
                "official source response exceeded the byte boundary",
                retryable=False,
                next_route="select-a-smaller-official-source",
            )
        media_type_header = _header(response["headers"], "content-type")
        media_type = (
            media_type_header.split(";", 1)[0].strip().lower()
            if media_type_header
            else ""
        )
        if media_type not in ALLOWED_MEDIA_TYPES:
            self._fail(
                "official_source_media_type_unsupported",
                "official source media type is unsupported",
                retryable=False,
                next_route="select-a-supported-official-source",
            )
        if media_type == "text/html" and self._is_js_only(body):
            self._fail(
                "official_source_js_only",
                "official source requires JavaScript and has no bounded document body",
                retryable=False,
                next_route="select-an-official-download-or-api",
            )
        accessed_at = self.wall_clock()
        if not isinstance(accessed_at, str) or not accessed_at.strip():
            raise ValueError("official source wall clock returned an invalid value")
        body_sha256 = _sha256_bytes(body)
        receipt = {
            "contract": "official_public_source_receipt/v1",
            "question_id": request["question_id"],
            "run_id": request["run_id"],
            "requested_url": requested_url,
            "final_url": current_url,
            "accessed_at": accessed_at.strip(),
            "status": response["status"],
            "media_type": media_type,
            "etag": _header(response["headers"], "etag"),
            "last_modified": _header(response["headers"], "last-modified"),
            "body_sha256": body_sha256,
            "body_bytes": len(body),
            "parse_location": request["parse_location"],
            "cache_hit": False,
            "question_external_read_count": read_count,
            "network_reads_this_request": read_count - initial_read_count,
        }
        if retrieval_fallback is not None:
            receipt["retrieval_fallback"] = retrieval_fallback
        if retrieval_resolution is not None:
            receipt["retrieval_resolution"] = retrieval_resolution
        predicted_cache_ref = {
            "contract": "official_public_source_cache_ref/v1",
            "path": (Path("objects") / f"{body_sha256}.bin").as_posix(),
            "sha256": body_sha256,
            "bytes": len(body),
        }
        receipt["source_binding"] = self._source_binding(
            receipt, predicted_cache_ref
        )
        receipt = self._receipt_with_request_bindings(request, receipt, body)
        if (
            cached is not None
            and cached["source_receipt"]["body_sha256"] != body_sha256
        ):
            self._fail(
                "official_source_drift",
                "official source bytes differ from the content-bound cache",
                retryable=False,
                next_route="review-source-version-before-replacement",
            )
        cache_ref = self._write_cache(request, receipt, body)
        return {
            "content": self._content(request, receipt, cache_ref, body),
            "source_identity": current_url,
            "source_version": f"sha256:{body_sha256}",
        }

    def with_runtime_timeout(
        self,
        timeout_seconds: float,
        *,
        remaining_seconds: Callable[[], float] = None,
        answer_timing: object = None,
    ) -> "OfficialPublicSourceCapability":
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("official source runtime timeout is invalid")
        clone = object.__new__(OfficialPublicSourceCapability)
        clone.cache_root = self.cache_root
        clone.transport = self.transport
        clone.wall_clock = self.wall_clock
        clone.max_bytes = self.max_bytes
        clone.timeout_seconds = min(self.timeout_seconds, float(timeout_seconds))
        clone.remaining_seconds = remaining_seconds or self.remaining_seconds
        clone.answer_timing = (
            answer_timing if answer_timing is not None else self.answer_timing
        )
        clone.source_index_path = self.source_index_path
        clone.source_index_sha256 = self.source_index_sha256
        clone.source_index = self.source_index
        return clone

    def _assert_source_index_integrity(self) -> None:
        try:
            observed = _sha256_bytes(self.source_index_path.read_bytes())
        except OSError:
            observed = None
        if observed != self.source_index_sha256:
            self._fail(
                "official_source_index_drift",
                "official source index drifted after runtime construction",
                retryable=False,
                next_route="rebuild-runtime-from-the-adopted-source-lock",
            )

    def with_remaining_budget(
        self, remaining_seconds: Callable[[], float]
    ) -> "OfficialPublicSourceCapability":
        return self.with_runtime_timeout(
            self.timeout_seconds,
            remaining_seconds=remaining_seconds,
        )

    def with_answer_timing(
        self, answer_timing: object
    ) -> "OfficialPublicSourceCapability":
        return self.with_runtime_timeout(
            self.timeout_seconds,
            answer_timing=answer_timing,
        )

    @property
    def handles_answer_timing(self) -> bool:
        return self.answer_timing is not None

    def _remaining_timeout_seconds(self) -> float:
        remaining = None
        if self.remaining_seconds is not None:
            remaining = self.remaining_seconds()
            if (
                isinstance(remaining, bool)
                or not isinstance(remaining, (int, float))
                or remaining < 0
            ):
                raise ValueError(
                    "official source remaining deadline is invalid"
                )
            if remaining <= 0:
                self._fail(
                    "official_source_deadline_exhausted",
                    "official source answer deadline is exhausted",
                    retryable=False,
                    next_route="return-bounded-conclusion-or-gap",
                )
        return min(
            self.timeout_seconds,
            float(remaining) if remaining is not None else self.timeout_seconds,
        )

    def _timing_start(self, activity_id: str) -> float | None:
        if self.answer_timing is None:
            return None
        return self.answer_timing.start_activity(activity_id)

    def _record_transport_activity(
        self,
        activity_id: str,
        *,
        kind: str,
        started: float | None,
        useful: bool,
    ) -> None:
        if self.answer_timing is None or started is None:
            return
        self.answer_timing.record_activity_since(
            activity_id,
            kind=kind,
            started_monotonic=started,
            useful=useful,
        )

    def _deadline_exhausted(self) -> bool:
        if self.remaining_seconds is None:
            return False
        remaining = self.remaining_seconds()
        if (
            isinstance(remaining, bool)
            or not isinstance(remaining, (int, float))
            or remaining < 0
        ):
            raise ValueError("official source remaining deadline is invalid")
        return remaining <= 0

    def _transport_request_with_retry(
        self,
        request: Mapping[str, object],
        url: str,
    ) -> tuple[dict, int]:
        last_code = None
        read_count = self._read_count(request)
        for attempt in (1, 2):
            timeout_seconds = self._remaining_timeout_seconds()
            read_count = self._consume_read_slot(request)
            activity_id = (
                f"official-source:{request['question_id']}:{request['run_id']}:"
                f"{read_count}"
            )
            started = self._timing_start(activity_id)
            try:
                raw = self.transport.request(
                    request["method"],
                    url,
                    timeout_seconds=timeout_seconds,
                    max_bytes=self.max_bytes,
                    headers={"Accept": ", ".join(sorted(ALLOWED_MEDIA_TYPES))},
                )
            except TimeoutError:
                last_code = "official_source_timeout"
                if attempt == 1 and not self._deadline_exhausted():
                    self._record_transport_activity(
                        activity_id,
                        kind="retry",
                        started=started,
                        useful=False,
                    )
                    continue
                self._record_transport_activity(
                    activity_id,
                    kind="external_read",
                    started=started,
                    useful=False,
                )
                if self._deadline_exhausted():
                    self._fail(
                        "official_source_deadline_exhausted",
                        "official source attempt consumed the answer deadline",
                        retryable=False,
                        next_route="return-bounded-conclusion-or-gap",
                    )
                self._fail(
                    last_code,
                    "official source timed out after a bounded retry",
                    retryable=True,
                    next_route="retry-once-or-return-bounded-source-gap",
                )
            except OSError:
                last_code = "official_source_transport_failure"
                if attempt == 1 and not self._deadline_exhausted():
                    self._record_transport_activity(
                        activity_id,
                        kind="retry",
                        started=started,
                        useful=False,
                    )
                    continue
                self._record_transport_activity(
                    activity_id,
                    kind="external_read",
                    started=started,
                    useful=False,
                )
                if self._deadline_exhausted():
                    self._fail(
                        "official_source_deadline_exhausted",
                        "official source attempt consumed the answer deadline",
                        retryable=False,
                        next_route="return-bounded-conclusion-or-gap",
                    )
                self._fail(
                    last_code,
                    "official source transport failed after a bounded retry",
                    retryable=True,
                    next_route="retry-once-or-return-bounded-source-gap",
                )
            response = self._validate_transport_response(raw)
            if self._deadline_exhausted():
                self._record_transport_activity(
                    activity_id,
                    kind="external_read",
                    started=started,
                    useful=False,
                )
                self._fail(
                    "official_source_deadline_exhausted",
                    "official source attempt consumed the answer deadline",
                    retryable=False,
                    next_route="return-bounded-conclusion-or-gap",
                )
            if response["status"] == 429 and attempt == 1:
                last_code = "official_source_rate_limited"
                self._record_transport_activity(
                    activity_id,
                    kind="retry",
                    started=started,
                    useful=False,
                )
                continue
            self._record_transport_activity(
                activity_id,
                kind="external_read",
                started=started,
                useful=200 <= response["status"] < 400,
            )
            return response, read_count
        self._fail(
            last_code or "official_source_transport_failure",
            "official source transport exhausted its bounded retry",
            retryable=True,
            next_route="retry-once-or-return-bounded-source-gap",
        )
        raise AssertionError("unreachable")

    def _fetch_final_response(
        self,
        request: Mapping[str, object],
        initial_url: str,
    ) -> tuple[dict, str, int]:
        current_url = initial_url
        requested_host = self._validate_url(initial_url)
        response = None
        read_count = self._read_count(request)
        for redirect_count in range(MAX_REDIRECTS + 1):
            response, read_count = self._transport_request_with_retry(
                request,
                current_url,
            )
            if response["status"] not in REDIRECT_STATUSES:
                break
            if redirect_count >= MAX_REDIRECTS:
                self._fail(
                    "official_source_redirect_limit",
                    "official source exceeded the redirect limit",
                    retryable=False,
                    next_route="return-bounded-source-gap",
                )
            location = _header(response["headers"], "location")
            if not location:
                self._fail(
                    "official_source_invalid_redirect",
                    "official source redirect lacks a location",
                    retryable=False,
                    next_route="return-bounded-source-gap",
                )
            redirected = urllib.parse.urljoin(current_url, location)
            redirect_host = self._validate_url(redirected)
            if redirect_host != requested_host:
                self._fail(
                    "official_source_cross_host_redirect",
                    "official source redirect changed host",
                    retryable=False,
                    next_route="return-bounded-source-gap",
                )
            current_url = redirected
        if response is None:
            raise RuntimeError("official source transport produced no response")
        return response, current_url, read_count

    def _retrieval_urls(self, requested_url: str) -> tuple[str, ...]:
        matches = [
            row
            for row in self.source_index["sources"]
            if row["url"] == requested_url
        ]
        if len(matches) != 1:
            return ()
        return tuple(matches[0].get("retrieval_urls", ()))

    def _next_section_parse_location(
        self,
        requested_url: str,
        parse_location: str,
    ) -> str | None:
        """Return the next indexed Annex heading for bounded completeness."""

        normalized = re.sub(r"\s+", " ", parse_location).strip().casefold()
        if re.match(r"^annex\s+[ivxlcdm]+\b", normalized) is None:
            return None
        matches = [
            row
            for row in self.source_index["sources"]
            if row["url"] == requested_url
        ]
        if len(matches) != 1:
            return None
        hints = matches[0].get("parse_hints")
        if not isinstance(hints, list):
            return None
        for index, hint in enumerate(hints):
            if (
                isinstance(hint, str)
                and re.sub(r"\s+", " ", hint).strip().casefold()
                == normalized
            ):
                for following in hints[index + 1 :]:
                    if (
                        isinstance(following, str)
                        and re.match(
                            r"^annex\s+[ivxlcdm]+\b",
                            re.sub(r"\s+", " ", following)
                            .strip()
                            .casefold(),
                        )
                    ):
                        return following
                return None
        return None

    @staticmethod
    def _is_js_only(body: bytes) -> bool:
        text = body[:65536].decode("utf-8", errors="ignore").lower()
        markers = (
            "enable javascript",
            "javascript is required",
            "requires javascript",
        )
        return any(marker in text for marker in markers)

    def _validate_request(self, payload: Mapping[str, object]) -> dict:
        if (
            not isinstance(payload, Mapping)
            or payload.get("contract") != REQUEST_CONTRACT
        ):
            raise ValueError("official source request contract is unsupported")
        if set(payload) - REQUEST_FIELDS:
            raise ValueError(
                "official source request contains unsupported or credential-bearing fields"
            )
        request = deepcopy(dict(payload))
        for key in ("question_id", "run_id"):
            if (
                not isinstance(request.get(key), str)
                or IDENTITY_RE.fullmatch(request[key]) is None
            ):
                raise ValueError(f"official source {key} is invalid")
        method = request.get("method")
        if not isinstance(method, str) or method.upper() not in ALLOWED_METHODS:
            raise ValueError("official source method must be GET or HEAD")
        request["method"] = method.upper()
        if not isinstance(request.get("url"), str):
            raise ValueError("official source URL is absent")
        self._validate_url(request["url"])
        if (
            not isinstance(request.get("parse_location"), str)
            or not request["parse_location"].strip()
        ):
            raise ValueError("official source parse location is absent")
        request["parse_location"] = request["parse_location"].strip()
        if "refresh" in request and not isinstance(request["refresh"], bool):
            raise ValueError("official source refresh flag is invalid")
        extraction = request.get("extraction")
        if extraction is not None:
            if (
                not isinstance(extraction, Mapping)
                or extraction.get("kind") not in {"numeric", "attachment"}
                or not isinstance(extraction.get("composite_key"), Mapping)
                or set(extraction["composite_key"]) != COMPOSITE_KEY_FIELDS
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in extraction["composite_key"].values()
                )
            ):
                self._fail(
                    "official_source_invalid_extraction",
                    "official source extraction lacks a complete composite key",
                    retryable=False,
                    next_route="repair-extraction-metadata",
                )
            request["extraction"] = deepcopy(dict(extraction))
        if "industry_summary" in request and not isinstance(
            request["industry_summary"], bool
        ):
            raise ValueError("official source industry_summary flag is invalid")
        return request

    def _read_cache(self, request: Mapping[str, object]) -> dict | None:
        metadata_path = self._cache_metadata_path(request)
        if not metadata_path.is_file():
            return None
        try:
            cached = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("official source cache metadata is invalid") from error
        if (
            not isinstance(cached, Mapping)
            or cached.get("contract") != "official_public_source_cache_entry/v1"
            or cached.get("request_key_sha256")
            != self._request_key_sha256(request)
            or not isinstance(cached.get("source_receipt"), Mapping)
            or not isinstance(cached.get("cache_ref"), Mapping)
        ):
            raise ValueError("official source cache metadata drifted")
        unsigned = deepcopy(dict(cached))
        entry_sha256 = unsigned.pop("entry_sha256", None)
        if entry_sha256 != hashlib.sha256(_canonical_bytes(unsigned)).hexdigest():
            self._fail(
                "official_source_cache_metadata_drift",
                "official source cache metadata hash drifted",
                retryable=False,
                next_route="repair-content-addressed-source-cache",
            )
        receipt = cached["source_receipt"]
        receipt_unsigned = deepcopy(dict(receipt))
        receipt_sha256 = receipt_unsigned.pop("receipt_sha256", None)
        if (
            receipt_sha256
            != hashlib.sha256(_canonical_bytes(receipt_unsigned)).hexdigest()
            or receipt.get("requested_url") != request["url"]
            or receipt.get("body_bytes") != cached["cache_ref"].get("bytes")
        ):
            self._fail(
                "official_source_cache_metadata_drift",
                "official source cache receipt drifted",
                retryable=False,
                next_route="repair-content-addressed-source-cache",
            )
        cache_ref = cached["cache_ref"]
        path = self.cache_root / str(cache_ref.get("path"))
        if (
            not path.is_file()
            or path.stat().st_size != cache_ref.get("bytes")
            or _sha256_bytes(path.read_bytes()) != cache_ref.get("sha256")
            or cache_ref.get("sha256")
            != cached["source_receipt"].get("body_sha256")
        ):
            self._fail(
                "official_source_cache_drift",
                "official source cached bytes drifted",
                retryable=False,
                next_route="repair-content-addressed-source-cache",
            )
        return deepcopy(dict(cached))

    def _write_cache(
        self,
        request: Mapping[str, object],
        receipt: Mapping[str, object],
        body: bytes,
    ) -> dict:
        body_sha256 = _sha256_bytes(body)
        relative = Path("objects") / f"{body_sha256}.bin"
        object_path = self.cache_root / relative
        if object_path.is_file():
            if object_path.read_bytes() != body:
                raise ValueError("official source content-addressed object drifted")
        else:
            _atomic_write(object_path, body)
        cache_ref = {
            "contract": "official_public_source_cache_ref/v1",
            "path": relative.as_posix(),
            "sha256": body_sha256,
            "bytes": len(body),
        }
        entry = {
            "contract": "official_public_source_cache_entry/v1",
            "request_key_sha256": self._request_key_sha256(request),
            "source_receipt": deepcopy(dict(receipt)),
            "cache_ref": cache_ref,
        }
        entry["entry_sha256"] = hashlib.sha256(_canonical_bytes(entry)).hexdigest()
        _atomic_write(
            self._cache_metadata_path(request),
            _canonical_bytes(entry) + b"\n",
        )
        return deepcopy(cache_ref)

    def _cached_result(
        self,
        cached: Mapping[str, object],
        request: Mapping[str, object],
    ) -> dict:
        receipt = deepcopy(dict(cached["source_receipt"]))
        receipt.update(
            {
                "question_id": request["question_id"],
                "run_id": request["run_id"],
                "parse_location": request["parse_location"],
                "cache_hit": True,
                "question_external_read_count": self._read_count(request),
                "network_reads_this_request": 0,
            }
        )
        body = (self.cache_root / str(cached["cache_ref"]["path"])).read_bytes()
        receipt["source_binding"] = self._source_binding(
            receipt, cached["cache_ref"]
        )
        receipt = self._receipt_with_request_bindings(request, receipt, body)
        return {
            "content": self._content(
                request,
                receipt,
                deepcopy(dict(cached["cache_ref"])),
                body,
            ),
            "source_identity": receipt["final_url"],
            "source_version": f"sha256:{receipt['body_sha256']}",
        }

    def _content(
        self,
        request: Mapping[str, object],
        receipt: Mapping[str, object],
        cache_ref: Mapping[str, object],
        body: bytes,
    ) -> dict:
        content = {
            "contract": "official_public_source_content/v1",
            "source_receipt": deepcopy(dict(receipt)),
            "cache_ref": deepcopy(dict(cache_ref)),
            "source_binding": deepcopy(dict(receipt["source_binding"])),
            "broad_claim_eligible": (
                request.get("industry_summary") is True
                and isinstance(receipt.get("exception_scan"), Mapping)
                and receipt["exception_scan"].get("scan_complete") is True
            ),
        }
        if isinstance(request.get("extraction"), Mapping):
            content["extraction_request"] = deepcopy(dict(request["extraction"]))
            content["extraction_binding"] = {
                "contract": "official_source_extraction_binding/v1",
                "status": "metadata_only_unverified",
                "kind": request["extraction"]["kind"],
                "composite_key": deepcopy(
                    dict(request["extraction"]["composite_key"])
                ),
                "source_body_sha256": receipt["body_sha256"],
                "parse_location": request["parse_location"],
                "may_support_numeric_claim": False,
            }
        if isinstance(receipt.get("exception_scan"), Mapping):
            content["exception_scan"] = deepcopy(dict(receipt["exception_scan"]))
        if request["method"] == "GET" and receipt.get(
            "media_type"
        ) in TEXT_MEDIA_TYPES:
            located = self._locate_excerpt(
                body,
                media_type=str(receipt["media_type"]),
                parse_location=str(request["parse_location"]),
                section_end_parse_location=self._next_section_parse_location(
                    str(request["url"]),
                    str(request["parse_location"]),
                ),
            )
            excerpt_bytes = located["text"].encode("utf-8")
            content["content_excerpt"] = {
                "contract": "official_public_source_excerpt/v1",
                "text": located["text"],
                "bytes": len(excerpt_bytes),
                "truncated": located["truncated"],
                "source_body_sha256": receipt["body_sha256"],
                "parse_location": request["parse_location"],
                "selection": located["selection"],
                "matched_terms": located["matched_terms"],
                "text_start": located["text_start"],
                "text_end": located["text_end"],
                "section_complete": located["section_complete"],
                "section_end_parse_location": located[
                    "section_end_parse_location"
                ],
                "source_document_truncated": located[
                    "source_document_truncated"
                ],
                "excerpt_sha256": located["excerpt_sha256"],
            }
        elif request["method"] == "GET":
            content["content_artifact_ref"] = {
                **deepcopy(dict(cache_ref)),
                "media_type": receipt["media_type"],
                "source_body_sha256": receipt["body_sha256"],
                "read_only": True,
            }
        return content

    def _source_binding(
        self,
        receipt: Mapping[str, object],
        cache_ref: Mapping[str, object],
    ) -> dict:
        matches = [
            row
            for row in self.source_index["sources"]
            if row["url"] == receipt["requested_url"]
        ]
        if len(matches) == 1:
            metadata = matches[0]
            source_id = str(metadata["source_id"])
            authority = str(metadata["authority"])
            declared_version = str(metadata["declared_version"])
        else:
            source_id = (
                "unindexed-official-url:"
                + hashlib.sha256(
                    str(receipt["requested_url"]).encode("utf-8")
                ).hexdigest()[:24]
            )
            authority = "official_public_host:" + urllib.parse.urlsplit(
                str(receipt["requested_url"])
            ).hostname
            declared_version = "unversioned-live-locator"
        return build_official_source_observation(
            source_id=source_id,
            authority=authority,
            declared_version=declared_version,
            requested_url=str(receipt["requested_url"]),
            final_url=str(receipt["final_url"]),
            parse_location=str(receipt["parse_location"]),
            observed_at=str(receipt["accessed_at"]),
            body_sha256=str(receipt["body_sha256"]),
            body_bytes=int(receipt["body_bytes"]),
            media_type=str(receipt["media_type"]),
            http_status=int(receipt["status"]),
            etag=receipt.get("etag"),
            last_modified=receipt.get("last_modified"),
            cache_ref=cache_ref,
        )

    def _receipt_with_request_bindings(
        self,
        request: Mapping[str, object],
        receipt: Mapping[str, object],
        body: bytes,
    ) -> dict:
        bound = deepcopy(dict(receipt))
        bound["extraction_request"] = deepcopy(request.get("extraction"))
        bound["exception_scan"] = (
            self._mechanical_exception_scan(
                body,
                str(bound["body_sha256"]),
                str(bound["media_type"]),
            )
            if request.get("industry_summary") is True
            else None
        )
        if request["method"] == "HEAD":
            bound["parse_binding"] = {
                "contract": "official_source_parse_binding/v1",
                "selection": "health_check_only",
                "parse_location": request["parse_location"],
                "matched_terms": [],
                "excerpt_sha256": None,
                "may_support_claim": False,
            }
        elif bound.get("media_type") in TEXT_MEDIA_TYPES:
            located = self._locate_excerpt(
                body,
                media_type=str(bound["media_type"]),
                parse_location=str(request["parse_location"]),
                section_end_parse_location=self._next_section_parse_location(
                    str(request["url"]),
                    str(request["parse_location"]),
                ),
            )
            bound["parse_binding"] = {
                "contract": "official_source_parse_binding/v1",
                "selection": located["selection"],
                "parse_location": request["parse_location"],
                "matched_terms": located["matched_terms"],
                "excerpt_sha256": located["excerpt_sha256"],
                "text_start": located["text_start"],
                "text_end": located["text_end"],
                "section_complete": located["section_complete"],
                "section_end_parse_location": located[
                    "section_end_parse_location"
                ],
                "may_support_claim": True,
            }
        else:
            bound["parse_binding"] = {
                "contract": "official_source_parse_binding/v1",
                "selection": "artifact_only_unparsed",
                "parse_location": request["parse_location"],
                "matched_terms": [],
                "excerpt_sha256": None,
                "may_support_claim": False,
            }
        bound.pop("receipt_sha256", None)
        bound["receipt_sha256"] = hashlib.sha256(
            _canonical_bytes(bound)
        ).hexdigest()
        return bound

    @staticmethod
    def _visible_text(body: bytes, media_type: str) -> str:
        text = body.decode("utf-8", errors="replace")
        if media_type in {"text/html", "application/xhtml+xml"}:
            parser = _VisibleTextParser()
            try:
                parser.feed(text)
                parser.close()
            except Exception as error:
                raise CapabilityGatewayError(
                    "official_source_parse_failure",
                    "official source HTML could not be safely parsed",
                    retryable=False,
                    next_route="select-an-official-download-or-api",
                ) from error
            text = "\n".join(parser.parts)
        return re.sub(r"\s+", " ", text).strip()

    def _locate_excerpt(
        self,
        body: bytes,
        *,
        media_type: str,
        parse_location: str,
        section_end_parse_location: str | None = None,
    ) -> dict:
        text = self._visible_text(body, media_type)
        if not text:
            self._fail(
                "official_source_empty_body",
                "official source contains no readable evidence text",
                retryable=False,
                next_route="select-an-official-document-with-content",
            )
        normalized_location = re.sub(
            r"\s+", " ", parse_location
        ).strip().casefold()
        if normalized_location in GENERIC_PARSE_LOCATIONS:
            start = 0
            selection = "document_prefix"
            matched_terms: list[str] = []
        else:
            terms = []
            for term in re.findall(r"[\w-]+", normalized_location):
                if (
                    term not in PARSE_STOP_WORDS
                    and (len(term) >= 3 or term.isdigit())
                    and term not in terms
                ):
                    terms.append(term)
            lowered = text.casefold()
            required_matches = min(
                len(terms),
                max(2, (3 * len(terms) + 4) // 5),
            )
            exact_position = lowered.find(normalized_location)
            if exact_position >= 0:
                start = exact_position
                matched_terms = list(terms)
            else:
                matched = []
                for term in terms:
                    position = lowered.find(term)
                    if position >= 0:
                        matched.append((term, position))
                if not terms or len(matched) < required_matches:
                    self._fail(
                        "official_source_parse_location_not_found",
                        "official source did not contain the requested parse location",
                        retryable=False,
                        next_route="select-an-indexed-parse-hint",
                    )
                candidates = []
                for _, anchor in matched:
                    candidate_start = max(0, anchor - 2048)
                    candidate_text = text[
                        candidate_start : candidate_start + MAX_EXCERPT_BYTES
                    ].casefold()
                    candidate_terms = [
                        term for term, _ in matched if term in candidate_text
                    ]
                    candidates.append(
                        (
                            len(candidate_terms),
                            -candidate_start,
                            candidate_start,
                            candidate_terms,
                        )
                    )
                _, _, start, matched_terms = max(candidates)
            if len(matched_terms) < required_matches:
                self._fail(
                    "official_source_parse_location_not_found",
                    "official source parse-location terms did not co-occur",
                    retryable=False,
                    next_route="select-an-indexed-parse-hint",
                )
            selection = "parse_location_match"
        section_end = None
        if (
            normalized_location not in GENERIC_PARSE_LOCATIONS
            and isinstance(section_end_parse_location, str)
        ):
            normalized_end = re.sub(
                r"\s+", " ", section_end_parse_location
            ).strip().casefold()
            annex_match = re.match(
                r"^annex\s+([ivxlcdm]+)\b",
                normalized_end,
            )
            if annex_match is not None:
                end_match = re.search(
                    rf"\bannex\s+{re.escape(annex_match.group(1))}\b",
                    lowered[start + len(normalized_location) :],
                )
                if end_match is not None:
                    section_end = (
                        start
                        + len(normalized_location)
                        + end_match.start()
                    )
        encoded_prefix = text[:start].encode("utf-8")
        selection_end = section_end if section_end is not None else len(text)
        remaining = text[start:selection_end]
        excerpt_bytes = remaining.encode("utf-8")[:MAX_EXCERPT_BYTES]
        while True:
            try:
                excerpt = excerpt_bytes.decode("utf-8")
                break
            except UnicodeDecodeError:
                excerpt_bytes = excerpt_bytes[:-1]
        end = start + len(excerpt)
        if normalized_location not in GENERIC_PARSE_LOCATIONS:
            excerpt_folded = excerpt.casefold()
            matched_terms = [
                term for term in matched_terms if term in excerpt_folded
            ]
            if len(matched_terms) < required_matches:
                self._fail(
                    "official_source_parse_location_not_found",
                    "official source parse-location terms fell outside the excerpt",
                    retryable=False,
                    next_route="select-an-indexed-parse-hint",
                )
        return {
            "text": excerpt,
            "selection": selection,
            "matched_terms": matched_terms,
            "text_start": start,
            "text_end": end,
            "source_text_prefix_bytes": len(encoded_prefix),
            "truncated": end < selection_end,
            "section_complete": (
                section_end is not None and end == section_end
            ),
            "section_end_parse_location": (
                section_end_parse_location if section_end is not None else None
            ),
            "source_document_truncated": end < len(text),
            "excerpt_sha256": _sha256_bytes(excerpt_bytes),
        }

    @classmethod
    def _load_source_index(cls, path: Path) -> dict:
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("official source index is unavailable or invalid") from error
        if (
            not isinstance(value, Mapping)
            or value.get("contract") != SOURCE_INDEX_CONTRACT
            or set(value) != {"contract", "index_id", "authority", "sources"}
            or not isinstance(value.get("index_id"), str)
            or not value["index_id"].strip()
            or not isinstance(value.get("authority"), str)
            or not value["authority"].strip()
            or not isinstance(value.get("sources"), list)
            or not value["sources"]
        ):
            raise ValueError("official source index contract is invalid")
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        normalized_sources = []
        for row in value["sources"]:
            if (
                not isinstance(row, Mapping)
                or not SOURCE_INDEX_FIELDS.issubset(row)
                or set(row)
                - (SOURCE_INDEX_FIELDS | SOURCE_INDEX_OPTIONAL_FIELDS)
            ):
                raise ValueError("official source index row is invalid")
            normalized = deepcopy(dict(row))
            for key in SOURCE_INDEX_FIELDS - {"topics", "parse_hints"}:
                if not isinstance(normalized.get(key), str) or not normalized[
                    key
                ].strip():
                    raise ValueError("official source index text field is invalid")
                normalized[key] = normalized[key].strip()
            for key in ("topics", "parse_hints"):
                items = normalized.get(key)
                if (
                    not isinstance(items, list)
                    or not items
                    or any(
                        not isinstance(item, str) or not item.strip()
                        for item in items
                    )
                ):
                    raise ValueError("official source index list field is invalid")
                normalized[key] = [item.strip() for item in items]
            cls._validate_url(normalized["url"])
            retrieval_urls = normalized.get("retrieval_urls")
            if retrieval_urls is not None:
                if (
                    not isinstance(retrieval_urls, list)
                    or not retrieval_urls
                    or len(retrieval_urls) > 3
                    or any(
                        not isinstance(item, str) or not item.strip()
                        for item in retrieval_urls
                    )
                ):
                    raise ValueError(
                        "official source retrieval URL list is invalid"
                    )
                normalized["retrieval_urls"] = [
                    item.strip() for item in retrieval_urls
                ]
                if (
                    normalized["url"] in normalized["retrieval_urls"]
                    or len(set(normalized["retrieval_urls"]))
                    != len(normalized["retrieval_urls"])
                ):
                    raise ValueError(
                        "official source retrieval URL is duplicated"
                    )
                for retrieval_url in normalized["retrieval_urls"]:
                    cls._validate_url(retrieval_url)
            if (
                normalized["source_id"] in seen_ids
                or normalized["url"] in seen_urls
            ):
                raise ValueError("official source index identity is duplicated")
            seen_ids.add(normalized["source_id"])
            seen_urls.add(normalized["url"])
            normalized_sources.append(normalized)
        return {
            "contract": SOURCE_INDEX_CONTRACT,
            "index_id": value["index_id"].strip(),
            "authority": value["authority"].strip(),
            "sources": normalized_sources,
        }

    @staticmethod
    def _mechanical_exception_scan(
        body: bytes, body_sha256: str, media_type: str
    ) -> dict:
        if media_type not in TEXT_MEDIA_TYPES:
            return {
                "contract": "official_source_exception_scan_result/v1",
                "basis": "mechanical_body_scan",
                "patterns": ["except", "only", "N/A", "exact_cn_exceptions"],
                "matches": {
                    "except": [],
                    "only": [],
                    "N/A": [],
                    "exact_cn_exceptions": [],
                },
                "source_body_sha256": body_sha256,
                "scan_complete": False,
                "failure_code": "non_text_media_not_scannable",
            }
        text = body.decode("utf-8", errors="replace")
        matches = {
            "except": re.findall(r"\bexcept\b", text, flags=re.IGNORECASE),
            "only": re.findall(r"\bonly\b", text, flags=re.IGNORECASE),
            "N/A": re.findall(r"\bN/A\b", text, flags=re.IGNORECASE),
            "exact_cn_exceptions": [],
        }
        exception_lines = [
            line
            for line in re.split(r"[\r\n.;]+", text)
            if re.search(r"\b(?:except|only|N/A)\b", line, flags=re.IGNORECASE)
        ]
        codes = []
        for line in exception_lines:
            for code in re.findall(r"\b\d{4}(?:\s+\d{2}){0,2}\b", line):
                normalized = " ".join(code.split())
                if normalized not in codes:
                    codes.append(normalized)
        matches["exact_cn_exceptions"] = codes
        return {
            "contract": "official_source_exception_scan_result/v1",
            "basis": "mechanical_body_scan",
            "patterns": ["except", "only", "N/A", "exact_cn_exceptions"],
            "matches": matches,
            "source_body_sha256": body_sha256,
            "scan_complete": True,
        }

    def _cache_metadata_path(self, request: Mapping[str, object]) -> Path:
        return self.cache_root / "sources" / (
            self._request_key_sha256(request) + ".json"
        )

    def _consume_read_slot(self, request: Mapping[str, object]) -> int:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        lock_path = self.cache_root / "question-read-ledger.lock"
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                ledger = self._read_ledger()
                identity = self._question_run_key(request)
                count = ledger["counts"].get(identity, 0)
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise ValueError("official source read ledger count is invalid")
                if count >= 6:
                    self._fail(
                        "official_source_read_limit",
                        "official source external-read limit is exhausted",
                        retryable=False,
                        next_route="return-bounded-source-gap",
                    )
                ledger["counts"][identity] = count + 1
                _atomic_write(
                    self.cache_root / "question-read-ledger.json",
                    _canonical_bytes(ledger) + b"\n",
                )
                return count + 1
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _read_count(self, request: Mapping[str, object]) -> int:
        ledger = self._read_ledger()
        value = ledger["counts"].get(self._question_run_key(request), 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("official source read ledger count is invalid")
        return value

    def _read_ledger(self) -> dict:
        path = self.cache_root / "question-read-ledger.json"
        if not path.is_file():
            return {
                "contract": "official_public_source_read_ledger/v1",
                "counts": {},
            }
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("official source read ledger is invalid") from error
        if (
            not isinstance(value, Mapping)
            or value.get("contract")
            != "official_public_source_read_ledger/v1"
            or not isinstance(value.get("counts"), Mapping)
        ):
            raise ValueError("official source read ledger drifted")
        return deepcopy(dict(value))

    @staticmethod
    def _question_run_key(request: Mapping[str, object]) -> str:
        return hashlib.sha256(
            _canonical_bytes(
                {
                    "question_id": request["question_id"],
                    "run_id": request["run_id"],
                }
            )
        ).hexdigest()

    @staticmethod
    def _request_key_sha256(request: Mapping[str, object]) -> str:
        return hashlib.sha256(
            _canonical_bytes(
                {
                    "method": request["method"],
                    "url": request["url"],
                }
            )
        ).hexdigest()

    @staticmethod
    def _validate_transport_response(value: object) -> dict:
        if not isinstance(value, Mapping):
            raise ValueError("official source transport response is invalid")
        status = value.get("status")
        headers = value.get("headers")
        body = value.get("body")
        if (
            isinstance(status, bool)
            or not isinstance(status, int)
            or not isinstance(headers, Mapping)
            or not isinstance(body, bytes)
        ):
            raise ValueError("official source transport response is invalid")
        return {
            "status": status,
            "headers": deepcopy(dict(headers)),
            "body": body,
        }

    @staticmethod
    def _validate_url(url: str) -> str:
        if not isinstance(url, str) or not url or any(
            character in url for character in ("\n", "\r", "\t")
        ):
            raise ValueError("official source URL is invalid")
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.hostname not in ALLOWED_HOSTS
            or parsed.fragment
        ):
            raise ValueError("official source URL is not an exact allowed HTTPS host")
        for raw_part in parsed.query.split("&"):
            raw_key = raw_part.split("=", 1)[0]
            decoded = raw_key
            for _ in range(4):
                next_value = urllib.parse.unquote_plus(decoded)
                if next_value == decoded:
                    break
                decoded = next_value
            normalized = re.sub(r"[^a-z0-9]", "", decoded.lower())
            if any(
                marker in normalized
                for marker in (
                    "token",
                    "password",
                    "secret",
                    "credential",
                    "authorization",
                    "apikey",
                    "accesskey",
                    "signature",
                    "cookie",
                    "session",
                )
            ):
                raise ValueError(
                    "official source URL contains a credential-bearing query key"
                )
        return str(parsed.hostname)

    @staticmethod
    def _fail(
        code: str,
        message: str,
        *,
        retryable: bool,
        next_route: str,
    ) -> None:
        raise CapabilityGatewayError(
            code,
            message,
            retryable=retryable,
            next_route=next_route,
        )
