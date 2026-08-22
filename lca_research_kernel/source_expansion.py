"""Expand public evidence discovery through official repositories and graphs."""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Dict, List, Sequence

try:
    from .public_discovery import _fetch_one, _json_bytes
except ImportError:  # Direct absolute-script execution from a project workspace.
    from public_discovery import _fetch_one, _json_bytes


JsonObject = Dict[str, object]

SOURCE_EXPANSION_CONTRACT = "lca_research_kernel.public_source_expansion/v1"
MANIFEST_CONTRACT = "lca_research_kernel.evidence_acquisition_manifest/v1"
ROUTES = {
    "openaire-doi-research-graph": {
        "source_class": "official_open_research_graph",
        "official_documentation_url": (
            "https://graph.openaire.eu/docs/apis/graph-api-v4/research-products/"
        ),
    },
    "europe-pmc-doi-repository": {
        "source_class": "official_domain_repository_index",
        "official_documentation_url": "https://europepmc.org/RestfulWebService",
    },
    "hal-doi-repository": {
        "source_class": "official_national_open_repository",
        "official_documentation_url": (
            "https://api.archives-ouvertes.fr/docs/search"
        ),
    },
}


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _normalize_doi(value: object) -> str:
    text = _required_text(value, "DOI").lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.strip()


def _normalize_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", _required_text(value, "title"))
    return " ".join(text.casefold().split())


def _artifact(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an artifact binding")
    path = _required_text(value.get("path"), f"{label}.path")
    sha256 = _required_text(value.get("sha256"), f"{label}.sha256")
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise ValueError(f"{label}.sha256 must be a lowercase sha256")
    return {"path": path, "sha256": sha256}


def _canonical_url(value: object) -> str:
    text = _required_text(value, "candidate URL")
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("candidate URL must be public HTTP(S)")
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, "")
    )


def _doi_values(value: object) -> List[str]:
    rows = value if isinstance(value, list) else [value]
    result = []
    for row in rows:
        if isinstance(row, str) and row.strip():
            result.append(_normalize_doi(row))
    return result


def _title_value(value: object) -> object:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _targets(manifest: JsonObject) -> List[JsonObject]:
    if manifest.get("contract") != MANIFEST_CONTRACT:
        raise ValueError("accepted evidence acquisition manifest is required")
    if manifest.get("status") != "lawful_evidence_acquisition_manifest_compiled":
        raise ValueError("evidence acquisition manifest is not current")
    targets = manifest.get("source_targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("manifest source targets are missing")
    return targets


def build_source_expansion_requests(manifest: JsonObject) -> List[JsonObject]:
    """Build three exact public-source requests for every accepted DOI target."""

    requests = []
    for target in _targets(manifest):
        if not isinstance(target, dict):
            raise ValueError("source target must be an object")
        target_id = _required_text(target.get("target_id"), "target_id")
        title = _required_text(target.get("title"), f"{target_id}.title")
        identifiers = target.get("identifiers")
        if not isinstance(identifiers, dict):
            raise ValueError(f"{target_id}.identifiers are missing")
        doi = _normalize_doi(identifiers.get("doi"))
        for route_id in sorted(ROUTES):
            if route_id == "openaire-doi-research-graph":
                url = "https://api.openaire.eu/graph/v4/research-products?" + (
                    urllib.parse.urlencode(
                        {"filter": f"ids.doi:{doi}", "pageSize": "5"}
                    )
                )
            elif route_id == "europe-pmc-doi-repository":
                url = (
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
                    + urllib.parse.urlencode(
                        {
                            "query": f"DOI:{doi}",
                            "format": "json",
                            "pageSize": "5",
                        }
                    )
                )
            else:
                url = "https://api.archives-ouvertes.fr/search/?" + (
                    urllib.parse.urlencode(
                        {
                            "q": f'doiId_s:"{doi}"',
                            "fl": (
                                "docid,title_s,doiId_s,fileMain_s,uri_s,"
                                "openAccess_bool,linkExtUrl_s,authIdHal_s,"
                                "authOrcidId_s,structName_s"
                            ),
                            "wt": "json",
                            "rows": "5",
                        }
                    )
                )
            requests.append(
                {
                    "target_id": target_id,
                    "route_id": route_id,
                    "requested_doi": doi,
                    "requested_title": title,
                    "request_url": url,
                    **ROUTES[route_id],
                }
            )
    return requests


def fetch_source_expansion(
    manifest: JsonObject,
    raw_root: Path,
    *,
    attempts: int = 3,
    timeout: int = 20,
) -> Dict[str, Dict[str, JsonObject]]:
    """Execute the bounded expansion routes and persist every response envelope."""

    if attempts < 1 or timeout < 1:
        raise ValueError("attempt and timeout limits must be positive")
    raw = {}
    for request in build_source_expansion_requests(manifest):
        envelope = _fetch_one(request, attempts=attempts, timeout=timeout)
        target_id = request["target_id"]
        route_id = request["route_id"]
        raw.setdefault(target_id, {})[route_id] = envelope
        path = raw_root / target_id / f"{route_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_json_bytes(envelope))
    return raw


def _failed_summary(envelope: JsonObject) -> JsonObject:
    return {
        "route_id": envelope.get("route_id"),
        "status": "route_failed",
        "http_status": envelope.get("http_status"),
        "attempt_count": envelope.get("attempt_count"),
        "error": envelope.get("error") or "missing JSON payload",
        "no_hit": False,
        "scientific_absence_proven": False,
        "public_location_candidates": [],
        "lawful_attachment_candidates": [],
        "author_route_anchors": [],
        "institution_route_anchors": [],
    }


def _open_access(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    label = str(value.get("label") or "").strip().upper()
    code = str(value.get("code") or "").strip().lower()
    return label in {"OPEN", "OPEN ACCESS", "OPEN SOURCE"} or code in {
        "c_abf2",
        "open",
    }


def _openaire_summary(
    envelope: JsonObject,
    target_id: str,
    doi: str,
    title: str,
) -> JsonObject:
    payload = envelope["payload"]
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise ValueError("OpenAIRE response is malformed")
    exact = []
    locations = []
    authors = []
    institutions = []
    abstract_hashes = []
    for row in results:
        if not isinstance(row, dict):
            continue
        pids = row.get("pids")
        returned_dois = []
        if isinstance(pids, list):
            for pid in pids:
                if (
                    isinstance(pid, dict)
                    and str(pid.get("scheme") or "").lower() == "doi"
                    and isinstance(pid.get("value"), str)
                ):
                    returned_dois.append(_normalize_doi(pid["value"]))
        if doi not in returned_dois:
            continue
        returned_title = row.get("mainTitle")
        if _normalize_title(returned_title) != _normalize_title(title):
            raise ValueError("OpenAIRE exact DOI identity conflict")
        exact.append({"record_id": row.get("id"), "title": returned_title})
        descriptions = row.get("descriptions")
        if isinstance(descriptions, list):
            for description in descriptions:
                if isinstance(description, str) and description.strip():
                    abstract_hashes.append(
                        hashlib.sha256(description.encode("utf-8")).hexdigest()
                    )
        for author in row.get("authors", []):
            if not isinstance(author, dict):
                continue
            pid = author.get("pid")
            identifier = pid.get("id") if isinstance(pid, dict) else None
            if (
                isinstance(identifier, dict)
                and str(identifier.get("scheme") or "").lower() == "orcid"
                and isinstance(identifier.get("value"), str)
            ):
                authors.append(
                    {
                        "target_id": target_id,
                        "route_id": envelope["route_id"],
                        "full_name": author.get("fullName"),
                        "orcid": identifier["value"].lower(),
                    }
                )
        for organization in row.get("organizations", []):
            if not isinstance(organization, dict):
                continue
            name = organization.get("legalName")
            if isinstance(name, str) and name.strip():
                institutions.append(
                    {
                        "target_id": target_id,
                        "route_id": envelope["route_id"],
                        "name": name.strip(),
                        "acronym": organization.get("acronym"),
                    }
                )
        for instance in row.get("instances", []):
            if not isinstance(instance, dict) or not _open_access(
                instance.get("accessRight")
            ):
                continue
            for url in instance.get("urls", []):
                if not isinstance(url, str) or not url.strip():
                    continue
                canonical = _canonical_url(url)
                if _normalize_doi(doi) in canonical.lower() and (
                    "doi.org" in canonical.lower()
                ):
                    continue
                locations.append(
                    {
                        "target_id": target_id,
                        "route_id": envelope["route_id"],
                        "url": canonical,
                        "candidate_kind": "public_location",
                        "access_right": instance.get("accessRight"),
                        "host": instance.get("hostedBy"),
                        "version": instance.get("type"),
                    }
                )
    no_hit = not locations
    return {
        "route_id": envelope["route_id"],
        "status": (
            "public_locations_found"
            if locations
            else (
                "exact_identity_found_no_public_location"
                if exact
                else "no_exact_record_found"
            )
        ),
        "http_status": envelope.get("http_status"),
        "attempt_count": envelope.get("attempt_count"),
        "response_sha256": envelope.get("response_sha256"),
        "exact_identity_records": exact,
        "metadata_abstract_sha256": sorted(set(abstract_hashes)),
        "metadata_abstract_admitted_as_field_evidence": False,
        "no_hit": no_hit,
        "scientific_absence_proven": False,
        "public_location_candidates": locations,
        "lawful_attachment_candidates": [],
        "author_route_anchors": authors,
        "institution_route_anchors": institutions,
    }


def _europe_pmc_summary(
    envelope: JsonObject,
    target_id: str,
    doi: str,
    title: str,
) -> JsonObject:
    payload = envelope["payload"]
    result_list = payload.get("resultList") if isinstance(payload, dict) else None
    results = result_list.get("result") if isinstance(result_list, dict) else None
    if not isinstance(results, list):
        raise ValueError("Europe PMC response is malformed")
    exact = []
    attachments = []
    for row in results:
        if not isinstance(row, dict) or not isinstance(row.get("doi"), str):
            continue
        if _normalize_doi(row["doi"]) != doi:
            continue
        returned_title = row.get("title")
        if _normalize_title(returned_title) != _normalize_title(title):
            raise ValueError("Europe PMC exact DOI identity conflict")
        exact.append(
            {
                "pmid": row.get("pmid"),
                "pmcid": row.get("pmcid"),
                "title": returned_title,
            }
        )
        pmcid = row.get("pmcid")
        if isinstance(pmcid, str) and pmcid.strip() and (
            str(row.get("inPMC") or "").upper() == "Y"
            or str(row.get("hasPDF") or "").upper() == "Y"
        ):
            attachments.append(
                {
                    "target_id": target_id,
                    "route_id": envelope["route_id"],
                    "url": (
                        "https://www.ebi.ac.uk/europepmc/webservices/rest/"
                        f"{urllib.parse.quote(pmcid.strip(), safe='')}/fullTextXML"
                    ),
                    "candidate_kind": "public_full_text_xml",
                    "version": "europe-pmc-full-text-xml",
                    "access_basis": "Europe PMC public full-text service",
                }
            )
    no_hit = not attachments
    return {
        "route_id": envelope["route_id"],
        "status": (
            "lawful_attachment_candidates_found"
            if attachments
            else (
                "exact_identity_found_no_public_attachment"
                if exact
                else "no_exact_record_found"
            )
        ),
        "http_status": envelope.get("http_status"),
        "attempt_count": envelope.get("attempt_count"),
        "response_sha256": envelope.get("response_sha256"),
        "exact_identity_records": exact,
        "no_hit": no_hit,
        "scientific_absence_proven": False,
        "public_location_candidates": [],
        "lawful_attachment_candidates": attachments,
        "author_route_anchors": [],
        "institution_route_anchors": [],
    }


def _hal_summary(
    envelope: JsonObject,
    target_id: str,
    doi: str,
    title: str,
) -> JsonObject:
    payload = envelope["payload"]
    response = payload.get("response") if isinstance(payload, dict) else None
    docs = response.get("docs") if isinstance(response, dict) else None
    if not isinstance(docs, list):
        raise ValueError("HAL response is malformed")
    exact = []
    attachments = []
    locations = []
    authors = []
    institutions = []
    for row in docs:
        if not isinstance(row, dict) or doi not in _doi_values(row.get("doiId_s")):
            continue
        returned_title = _title_value(row.get("title_s"))
        if _normalize_title(returned_title) != _normalize_title(title):
            raise ValueError("HAL exact DOI identity conflict")
        exact.append({"docid": row.get("docid"), "title": returned_title})
        is_open = row.get("openAccess_bool") is True
        file_url = row.get("fileMain_s")
        if is_open and isinstance(file_url, str) and file_url.strip():
            attachments.append(
                {
                    "target_id": target_id,
                    "route_id": envelope["route_id"],
                    "url": _canonical_url(file_url),
                    "candidate_kind": "public_repository_file",
                    "version": "HAL-deposited-file",
                    "access_basis": "HAL openAccess_bool=true",
                }
            )
        uri = row.get("uri_s")
        if (
            is_open
            and isinstance(uri, str)
            and uri.strip()
            and not attachments
        ):
            locations.append(
                {
                    "target_id": target_id,
                    "route_id": envelope["route_id"],
                    "url": _canonical_url(uri),
                    "candidate_kind": "public_location",
                    "access_basis": "HAL openAccess_bool=true",
                }
            )
        for orcid in row.get("authOrcidId_s", []):
            if isinstance(orcid, str) and orcid.strip():
                authors.append(
                    {
                        "target_id": target_id,
                        "route_id": envelope["route_id"],
                        "full_name": None,
                        "orcid": orcid.strip().lower(),
                    }
                )
        for name in row.get("structName_s", []):
            if isinstance(name, str) and name.strip():
                institutions.append(
                    {
                        "target_id": target_id,
                        "route_id": envelope["route_id"],
                        "name": name.strip(),
                        "acronym": None,
                    }
                )
    no_hit = not attachments and not locations
    return {
        "route_id": envelope["route_id"],
        "status": (
            "lawful_attachment_candidates_found"
            if attachments
            else (
                "public_locations_found"
                if locations
                else "no_exact_record_found"
            )
        ),
        "http_status": envelope.get("http_status"),
        "attempt_count": envelope.get("attempt_count"),
        "response_sha256": envelope.get("response_sha256"),
        "exact_identity_records": exact,
        "no_hit": no_hit,
        "scientific_absence_proven": False,
        "public_location_candidates": locations,
        "lawful_attachment_candidates": attachments,
        "author_route_anchors": authors,
        "institution_route_anchors": institutions,
    }


def _route_summary(
    envelope: JsonObject,
    target_id: str,
    doi: str,
    title: str,
) -> JsonObject:
    if envelope.get("error") or not isinstance(envelope.get("payload"), dict):
        return _failed_summary(envelope)
    route_id = envelope["route_id"]
    if route_id == "openaire-doi-research-graph":
        return _openaire_summary(envelope, target_id, doi, title)
    if route_id == "europe-pmc-doi-repository":
        return _europe_pmc_summary(envelope, target_id, doi, title)
    if route_id == "hal-doi-repository":
        return _hal_summary(envelope, target_id, doi, title)
    raise ValueError(f"unsupported expansion route: {route_id}")


def _prior_candidate_urls(previous_report: JsonObject) -> set[str]:
    urls = set()
    for target in previous_report.get("target_results", []):
        if not isinstance(target, dict):
            continue
        for key in (
            "lawful_attachment_candidates",
            "public_location_candidates",
        ):
            for candidate in target.get(key, []):
                if isinstance(candidate, dict) and isinstance(
                    candidate.get("url"), str
                ):
                    urls.add(_canonical_url(candidate["url"]))
    return urls


def compile_source_expansion_report(
    manifest: JsonObject,
    previous_report: JsonObject,
    raw_results: Dict[str, Dict[str, JsonObject]],
    manifest_binding: JsonObject,
    previous_report_binding: JsonObject,
) -> JsonObject:
    """Normalize official-source responses while keeping field evidence closed."""

    targets = _targets(manifest)
    if (
        previous_report.get("contract")
        != "lca_research_kernel.public_evidence_discovery/v1"
        or previous_report.get("routing", {}).get("next_internal_route")
        != "expand-public-source-discovery"
        or previous_report.get("scientific_field_evidence_admitted") is not False
    ):
        raise ValueError("accepted public discovery expansion route is required")
    if (
        previous_report.get("scope_id") != manifest.get("scope_id")
        or previous_report.get("revision") != manifest.get("revision")
    ):
        raise ValueError("previous report does not match manifest scope")
    route_ids = sorted(ROUTES)
    previous_urls = _prior_candidate_urls(previous_report)
    seen_urls = set(previous_urls)
    duplicate_count = 0
    failed_routes = []
    no_hit_count = 0
    successful_count = 0
    public_locations = []
    attachments = []
    author_anchors = []
    institution_anchors = []
    raw_refs = []
    target_results = []
    for target in targets:
        target_id = _required_text(target.get("target_id"), "target_id")
        title = _required_text(target.get("title"), f"{target_id}.title")
        identifiers = target.get("identifiers")
        if not isinstance(identifiers, dict):
            raise ValueError(f"{target_id}.identifiers are missing")
        doi = _normalize_doi(identifiers.get("doi"))
        target_raw = raw_results.get(target_id)
        if not isinstance(target_raw, dict) or set(target_raw) != set(route_ids):
            raise ValueError(f"{target_id} raw results do not cover expansion routes")
        route_results = []
        target_locations = []
        target_attachments = []
        target_authors = []
        target_institutions = []
        for route_id in route_ids:
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
                raise ValueError(f"{target_id}/{route_id} request identity conflict")
            summary = _route_summary(envelope, target_id, doi, title)
            route_results.append(summary)
            raw_refs.append(
                {
                    "target_id": target_id,
                    "route_id": route_id,
                    "path": f"raw/{target_id}/{route_id}.json",
                    "sha256": hashlib.sha256(_json_bytes(envelope)).hexdigest(),
                }
            )
            if summary["status"] == "route_failed":
                failed_routes.append(
                    {"target_id": target_id, "route_id": route_id}
                )
                continue
            successful_count += 1
            no_hit_count += int(summary["no_hit"] is True)
            for key, aggregate, target_aggregate in (
                (
                    "public_location_candidates",
                    public_locations,
                    target_locations,
                ),
                (
                    "lawful_attachment_candidates",
                    attachments,
                    target_attachments,
                ),
            ):
                for candidate in summary[key]:
                    url = _canonical_url(candidate["url"])
                    if url in seen_urls:
                        duplicate_count += 1
                        continue
                    seen_urls.add(url)
                    normalized = {**candidate, "url": url}
                    aggregate.append(normalized)
                    target_aggregate.append(normalized)
            target_authors.extend(summary["author_route_anchors"])
            target_institutions.extend(summary["institution_route_anchors"])
        author_anchors.extend(target_authors)
        institution_anchors.extend(target_institutions)
        target_results.append(
            {
                "target_id": target_id,
                "doi": doi,
                "title": title,
                "route_results": route_results,
                "public_location_candidates": target_locations,
                "lawful_attachment_candidates": target_attachments,
                "author_route_anchors": target_authors,
                "institution_route_anchors": target_institutions,
                "required_fields": [
                    {
                        "field_id": _required_text(field.get("field_id"), "field_id"),
                        "status": "unresolved",
                    }
                    for field in target.get("required_fields", [])
                    if isinstance(field, dict)
                ],
                "scientific_field_evidence_admitted": False,
            }
        )
    if attachments:
        next_route = "ingest-lawful-attachment-candidates"
    elif public_locations:
        next_route = "validate-public-location-candidates"
    elif author_anchors or institution_anchors:
        next_route = "derive-author-institution-public-routes"
    else:
        next_route = "expand-another-public-source-class"
    failed_count = len(failed_routes)
    status = (
        "public_source_expansion_completed_degraded"
        if failed_count
        else (
            "public_source_expansion_completed_candidates"
            if attachments or public_locations
            else "public_source_expansion_completed_no_candidate"
        )
    )
    return {
        "schema_version": 1,
        "contract": SOURCE_EXPANSION_CONTRACT,
        "scope_id": manifest.get("scope_id"),
        "revision": manifest.get("revision"),
        "status": status,
        "manifest": _artifact(manifest_binding, "manifest_binding"),
        "previous_public_discovery": _artifact(
            previous_report_binding, "previous_report_binding"
        ),
        "target_count": len(targets),
        "route_count": len(targets) * len(route_ids),
        "successful_route_count": successful_count,
        "failed_route_count": failed_count,
        "no_hit_count": no_hit_count,
        "duplicate_candidate_count": duplicate_count,
        "lawful_attachment_candidate_count": len(attachments),
        "public_location_candidate_count": len(public_locations),
        "author_route_anchor_count": len(author_anchors),
        "institution_route_anchor_count": len(institution_anchors),
        "lawful_attachment_candidates": attachments,
        "public_location_candidates": public_locations,
        "author_route_anchors": author_anchors,
        "institution_route_anchors": institution_anchors,
        "target_results": target_results,
        "raw_response_refs": raw_refs,
        "routing": {
            "retry_routes": failed_routes,
            "human_routes_untouched": sorted(
                manifest.get("routing", {}).get("human_routes", [])
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
                "Download and validate candidate bytes, hashes, versions, and access bases before parsing."
            ),
            "validate-public-location-candidates": (
                "Resolve public location URLs to lawful source bytes without admitting metadata as field evidence."
            ),
            "derive-author-institution-public-routes": (
                "Compile public author and institution repository routes from exact-identity ORCID and organization anchors."
            ),
            "expand-another-public-source-class": (
                "Add another lawful public source class while retaining all dated no-hit evidence."
            ),
        }[next_route],
    }


def _read_json(path: Path) -> JsonObject:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--previous-report", type=Path, required=True)
    parser.add_argument("--manifest-binding", type=Path, required=True)
    parser.add_argument("--previous-report-binding", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args(argv)
    manifest = _read_json(args.manifest)
    previous_report = _read_json(args.previous_report)
    raw = fetch_source_expansion(
        manifest,
        args.raw_root,
        attempts=args.attempts,
        timeout=args.timeout,
    )
    report = compile_source_expansion_report(
        manifest,
        previous_report,
        raw,
        _read_json(args.manifest_binding),
        _read_json(args.previous_report_binding),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(_json_bytes(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
