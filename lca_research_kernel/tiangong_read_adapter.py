"""Bounded, secret-safe read adapters for TianGong Open Data and TIDAS exports."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


JsonObject = Dict[str, object]

CATALOG_CAPTURE_CONTRACT = "lca_research_kernel.tiangong_open_data_capture/v1"
EXACT_PROCESS_BATCH_CONTRACT = (
    "lca_research_kernel.tiangong_exact_process_batch_capture/v1"
)
EXACT_FLOW_BATCH_CONTRACT = "lca_research_kernel.tiangong_exact_flow_batch_capture/v1"
PUBLIC_RESULT_BATCH_CONTRACT = (
    "lca_research_kernel.tiangong_public_result_batch_capture/v1"
)
TIDAS_EXPORT_INSPECTION_CONTRACT = (
    "lca_research_kernel.tiangong_official_tidas_export_inspection/v1"
)
REQUIRED_ENVIRONMENT_NAMES = (
    "TIANGONG_LCA_API_BASE_URL",
    "TIANGONG_LCA_API_KEY",
    "TIANGONG_LCA_SUPABASE_PUBLISHABLE_KEY",
)
_SECRET_BEARING_ENVIRONMENT_NAMES = (
    "TIANGONG_LCA_API_KEY",
    "TIANGONG_LCA_SUPABASE_PUBLISHABLE_KEY",
)
_PACKAGE_SPEC = re.compile(
    r"^@tiangong-lca/cli@(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)$"
)
_EXACT_DATASET_REF = re.compile(
    r"^(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})"
    r"@(?P<version>[0-9]{2}\.[0-9]{2}\.[0-9]{3})$",
    re.IGNORECASE,
)
_SUPPORTED_ROOTS = {
    "processDataSet": "process",
    "flowDataSet": "flow",
    "lifeCycleModelDataSet": "lifecyclemodel",
    "LCIAMethodDataSet": "lciamethod",
    "lciaMethodDataSet": "lciamethod",
}


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _exact_package_spec(value: object) -> str:
    if not isinstance(value, str) or not _PACKAGE_SPEC.fullmatch(value.strip()):
        raise ValueError("package_spec must be an exact @tiangong-lca/cli version")
    return value.strip()


def _write_json(path: Path, value: object) -> JsonObject:
    content = _json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != content:
        raise ValueError(f"existing artifact drifted: {path.name}")
    path.write_bytes(content)
    return {"path": path.name, "sha256": _sha256(content), "bytes": len(content)}


def _secret_echo(output: str, environment: Mapping[str, str]) -> bool:
    for name in _SECRET_BEARING_ENVIRONMENT_NAMES:
        value = environment.get(name)
        if isinstance(value, str) and value and value in output:
            return True
    api_base = environment.get("TIANGONG_LCA_API_BASE_URL")
    if (
        isinstance(api_base, str)
        and api_base
        and api_base in output
        and not _is_public_https_base(api_base)
    ):
        return True
    return False


def _is_public_https_base(value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port not in {None, 443}
    ):
        return False
    comparable = f"{parsed.hostname}{parsed.path}".casefold()
    return not any(
        marker in comparable
        for marker in ("api_key", "apikey", "password", "secret", "signature", "token")
    )


def _published_rows(payload: object) -> List[JsonObject]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        candidate = payload.get("rows")
        rows = candidate if isinstance(candidate, list) else []
    else:
        raise ValueError("process list output must be a JSON object or array")
    normalized = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"process list row {index} is not an object")
        state = row.get("stateCode", row.get("state_code", 100))
        if state not in {100, "100"}:
            raise ValueError("process list contains a non-published row")
        normalized.append(dict(row))
    return normalized


def capture_published_process_catalog(
    *,
    package_spec: str,
    output_dir: Path,
    environment: Mapping[str, str],
) -> JsonObject:
    """Capture the published Process catalog using one exact read-only CLI call."""

    package = _exact_package_spec(package_spec)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    present_names = sorted(
        name
        for name in REQUIRED_ENVIRONMENT_NAMES
        if isinstance(environment.get(name), str) and environment.get(name)
    )
    missing_names = sorted(set(REQUIRED_ENVIRONMENT_NAMES) - set(present_names))
    if missing_names:
        report: JsonObject = {
            "contract": CATALOG_CAPTURE_CONTRACT,
            "status": "credentials_required",
            "package_spec": package,
            "required_environment_names": sorted(REQUIRED_ENVIRONMENT_NAMES),
            "present_environment_names": present_names,
            "missing_environment_names": missing_names,
            "remote_read_executed_count": 0,
            "remote_write_executed_count": 0,
            "published_process_count": 0,
            "next_route": "ingest-user-exported-official-tidas-zip",
        }
        _write_json(output_dir / "capture-report.json", report)
        return report

    argv = [
        "npx",
        "--yes",
        package,
        "process",
        "list",
        "--state-code",
        "100",
        "--all",
        "--json",
    ]
    completed = subprocess.run(
        argv,
        shell=False,
        capture_output=True,
        text=True,
        check=False,
        env=dict(environment),
    )
    combined_output = f"{completed.stdout}\n{completed.stderr}"
    if _secret_echo(combined_output, environment):
        raise ValueError("secret value was echoed by the read-only command")
    if completed.returncode != 0:
        raise ValueError(f"published Process read failed with exit {completed.returncode}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("process list did not return valid JSON") from error
    rows = _published_rows(payload)
    raw_payload = {"package_spec": package, "rows": rows}
    raw_binding = _write_json(output_dir / "raw-process-list.json", raw_payload)
    report = {
        "contract": CATALOG_CAPTURE_CONTRACT,
        "status": "captured",
        "package_spec": package,
        "required_environment_names": sorted(REQUIRED_ENVIRONMENT_NAMES),
        "present_environment_names": present_names,
        "missing_environment_names": [],
        "argv": argv,
        "remote_read_executed_count": 1,
        "remote_write_executed_count": 0,
        "published_process_count": len(rows),
        "raw_process_list": raw_binding,
        "next_route": "select-tiangong-open-data-case",
    }
    _write_json(output_dir / "capture-report.json", report)
    return report


def _requested_exact_processes(candidates: Sequence[Mapping[str, object]]) -> List[JsonObject]:
    requested = []
    seen = set()
    for index, candidate in enumerate(candidates):
        exact_ref = candidate.get("exact_ref") if isinstance(candidate, dict) else None
        match = _EXACT_DATASET_REF.fullmatch(str(exact_ref or ""))
        if match is None:
            raise ValueError(f"candidates[{index}].exact_ref must be exact UUID@version")
        process_id = match.group("id").lower()
        version = match.group("version")
        exact = f"{process_id}@{version}"
        if exact in seen:
            raise ValueError("candidate exact Process references must be unique")
        seen.add(exact)
        requested.append(
            {"process_id": process_id, "version": version, "exact_ref": exact}
        )
    if not requested:
        raise ValueError("at least one exact Process candidate is required")
    return sorted(requested, key=lambda row: str(row["exact_ref"]))


def capture_exact_process_batch(
    *,
    package_spec: str,
    candidates: Sequence[Mapping[str, object]],
    output_dir: Path,
    environment: Mapping[str, str],
) -> JsonObject:
    """Read exact reviewed Process rows in version groups and validate official bytes."""

    package = _exact_package_spec(package_spec)
    requested = _requested_exact_processes(candidates)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    present_names = sorted(
        name
        for name in REQUIRED_ENVIRONMENT_NAMES
        if isinstance(environment.get(name), str) and environment.get(name)
    )
    missing_environment = sorted(set(REQUIRED_ENVIRONMENT_NAMES) - set(present_names))
    if missing_environment:
        report: JsonObject = {
            "contract": EXACT_PROCESS_BATCH_CONTRACT,
            "status": "credentials_required",
            "package_spec": package,
            "required_environment_names": sorted(REQUIRED_ENVIRONMENT_NAMES),
            "present_environment_names": present_names,
            "missing_environment_names": missing_environment,
            "requested_exact_process_count": len(requested),
            "resolved_exact_process_count": 0,
            "missing_exact_process_count": len(requested),
            "remote_read_executed_count": 0,
            "remote_write_executed_count": 0,
            "native_validation_command_count": 0,
            "next_route": "ingest-user-exported-official-tidas-zip",
        }
        _write_json(output_dir / "capture-report.json", report)
        return report

    by_version: Dict[str, List[str]] = {}
    for row in requested:
        by_version.setdefault(str(row["version"]), []).append(str(row["process_id"]))
    observed_rows: Dict[str, JsonObject] = {}
    group_reports = []
    for version in sorted(by_version):
        ids = sorted(by_version[version])
        argv = ["npx", "--yes", package, "process", "list"]
        for process_id in ids:
            argv.extend(["--id", process_id])
        argv.extend(
            ["--version", version, "--state-code", "100", "--all", "--json"]
        )
        completed = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            check=False,
            env=dict(environment),
            timeout=120,
        )
        combined_output = f"{completed.stdout}\n{completed.stderr}"
        if _secret_echo(combined_output, environment):
            raise ValueError("secret value was echoed by the exact Process read")
        group_report: JsonObject = {
            "version": version,
            "requested_process_count": len(ids),
            "returncode": completed.returncode,
            "response_row_count": 0,
        }
        if completed.returncode == 0:
            try:
                payload = json.loads(completed.stdout)
                rows = _published_rows(payload)
            except (json.JSONDecodeError, ValueError):
                rows = []
            group_report["response_row_count"] = len(rows)
            for row in rows:
                process_id = str(row.get("id", "")).lower()
                row_version = str(row.get("version", ""))
                exact = f"{process_id}@{row_version}"
                if process_id in ids and row_version == version and exact not in observed_rows:
                    if isinstance(row.get("process"), dict):
                        observed_rows[exact] = row
        group_reports.append(group_report)

    requested_refs = {str(row["exact_ref"]) for row in requested}
    resolved_refs = sorted(requested_refs & set(observed_rows))
    missing_refs = sorted(requested_refs - set(observed_rows))
    resolved_rows = [observed_rows[exact] for exact in resolved_refs]
    raw_binding = _write_json(
        output_dir / "exact-process-rows.json",
        {"package_spec": package, "rows": resolved_rows},
    )
    validation_command_count = 0
    validation_returncode = None
    validation_counts: JsonObject = {}
    if resolved_rows:
        validation_input = _write_json(
            output_dir / "validation-input.json",
            [row["process"] for row in resolved_rows],
        )
        validation_dir = output_dir / "validation"
        argv = [
            "npx",
            "--yes",
            package,
            "dataset",
            "validate",
            "--input",
            str((output_dir / validation_input["path"]).resolve()),
            "--type",
            "process",
            "--out-dir",
            str(validation_dir.resolve()),
            "--json",
        ]
        validation_environment = dict(environment)
        for name in REQUIRED_ENVIRONMENT_NAMES:
            validation_environment.pop(name, None)
        validation = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            check=False,
            env=validation_environment,
            timeout=120,
        )
        validation_command_count = 1
        validation_returncode = validation.returncode
        try:
            validation_payload = json.loads(validation.stdout)
        except json.JSONDecodeError:
            validation_payload = {}
        if isinstance(validation_payload, dict) and isinstance(
            validation_payload.get("counts"), dict
        ):
            validation_counts = dict(validation_payload["counts"])

    valid_count = validation_counts.get("valid", 0)
    invalid_count = validation_counts.get("invalid", 0)
    complete = not missing_refs and validation_returncode == 0 and invalid_count == 0
    report = {
        "contract": EXACT_PROCESS_BATCH_CONTRACT,
        "status": (
            "exact_process_batch_captured"
            if complete
            else "exact_process_batch_incomplete"
        ),
        "package_spec": package,
        "requested_exact_processes": requested,
        "requested_exact_process_count": len(requested),
        "resolved_exact_process_count": len(resolved_refs),
        "resolved_exact_processes": resolved_refs,
        "missing_exact_process_count": len(missing_refs),
        "missing_exact_processes": missing_refs,
        "group_reports": group_reports,
        "remote_read_executed_count": len(group_reports),
        "remote_write_executed_count": 0,
        "raw_process_rows": raw_binding,
        "native_validation_command_count": validation_command_count,
        "native_validation_returncode": validation_returncode,
        "native_validation_valid_count": valid_count,
        "native_validation_invalid_count": invalid_count,
        "next_route": (
            "bind-public-results-and-provider-snapshot-for-batch"
            if complete
            else "evaluate-next-public-result-root-batch"
        ),
    }
    _write_json(output_dir / "capture-report.json", report)
    return report


def capture_public_result_batch(
    *,
    endpoint: str,
    candidates: Sequence[Mapping[str, object]],
    output_dir: Path,
) -> JsonObject:
    """Capture exact public LCIA rows without credentials or remote writes."""

    parsed = urllib.parse.urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/functions/v1/data_product_results")
    ):
        raise ValueError("endpoint must be an exact HTTPS data_product_results route")
    requested = _requested_exact_processes(candidates)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    captured = []
    failed = []
    for row in requested:
        process_id = str(row["process_id"])
        version = str(row["version"])
        exact_ref = str(row["exact_ref"])
        request_body = _json_bytes(
            {"processId": process_id, "processVersion": version}
        )
        request = urllib.request.Request(
            endpoint,
            data=request_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = getattr(response, "status", 200)
                response_bytes = response.read()
            payload = json.loads(response_bytes)
        except (
            OSError,
            TimeoutError,
            UnicodeError,
            json.JSONDecodeError,
            urllib.error.URLError,
        ) as error:
            failed.append(
                {
                    "exact_ref": exact_ref,
                    "reason": "read_failed",
                    "error_type": type(error).__name__,
                }
            )
            continue
        data = payload.get("data") if isinstance(payload, dict) else None
        process = data.get("process") if isinstance(data, dict) else None
        observed_id = (
            str(process.get("processId", "")).lower()
            if isinstance(process, dict)
            else ""
        )
        observed_version = (
            str(process.get("processVersion", ""))
            if isinstance(process, dict)
            else ""
        )
        if (
            status != 200
            or not isinstance(payload, dict)
            or payload.get("ok") is not True
            or observed_id != process_id
            or observed_version != version
        ):
            failed.append(
                {
                    "exact_ref": exact_ref,
                    "reason": (
                        "identity_drift"
                        if observed_id or observed_version
                        else "unsuccessful_response"
                    ),
                    "http_status": status,
                }
            )
            continue
        filename = f"{process_id}_{version}.json"
        binding = _write_json(output_dir / filename, payload)
        captured.append(
            {
                "exact_ref": exact_ref,
                "response": binding,
                "http_status": status,
            }
        )

    complete = len(captured) == len(requested) and not failed
    report = {
        "contract": PUBLIC_RESULT_BATCH_CONTRACT,
        "status": (
            "public_result_batch_captured"
            if complete
            else "public_result_batch_incomplete"
        ),
        "endpoint": endpoint,
        "request_method": "POST",
        "credential_mode": "none_public_read",
        "requested_exact_result_count": len(requested),
        "resolved_exact_result_count": len(captured),
        "captured_exact_results": captured,
        "failed_exact_result_count": len(failed),
        "failed_exact_results": failed,
        "remote_read_executed_count": len(requested),
        "remote_write_executed_count": 0,
        "next_route": (
            "bind-public-results-to-official-lcia-index"
            if complete
            else "evaluate-next-public-result-root-batch"
        ),
    }
    _write_json(output_dir / "capture-report.json", report)
    return report


def _flow_rows(payload: object) -> List[JsonObject]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        rows = payload["rows"]
    elif isinstance(payload, dict):
        rows = [payload]
    else:
        raise ValueError("flow list output must be a JSON object or array")
    normalized = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("flow"), dict):
            raise ValueError(f"flow list row {index} is incomplete")
        state = row.get("state_code", row.get("stateCode", 100))
        if state not in {100, "100"}:
            raise ValueError("flow list contains a non-published row")
        normalized.append(dict(row))
    return normalized


def capture_exact_flow_batch(
    *,
    package_spec: str,
    flows: Sequence[Mapping[str, object]],
    output_dir: Path,
    environment: Mapping[str, str],
) -> JsonObject:
    """Read and validate exact reviewed Flow rows without exposing credentials."""

    package = _exact_package_spec(package_spec)
    requested = _requested_exact_processes(flows)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    present_names = sorted(
        name
        for name in REQUIRED_ENVIRONMENT_NAMES
        if isinstance(environment.get(name), str) and environment.get(name)
    )
    missing_environment = sorted(set(REQUIRED_ENVIRONMENT_NAMES) - set(present_names))
    if missing_environment:
        report: JsonObject = {
            "contract": EXACT_FLOW_BATCH_CONTRACT,
            "status": "credentials_required",
            "package_spec": package,
            "required_environment_names": sorted(REQUIRED_ENVIRONMENT_NAMES),
            "present_environment_names": present_names,
            "missing_environment_names": missing_environment,
            "requested_exact_flow_count": len(requested),
            "resolved_exact_flow_count": 0,
            "remote_read_executed_count": 0,
            "remote_write_executed_count": 0,
            "next_route": "ingest-user-exported-official-tidas-zip",
        }
        _write_json(output_dir / "capture-report.json", report)
        return report

    by_version: Dict[str, List[str]] = {}
    for row in requested:
        by_version.setdefault(str(row["version"]), []).append(str(row["process_id"]))
    observed_rows: Dict[str, JsonObject] = {}
    group_reports = []
    for version in sorted(by_version):
        ids = sorted(by_version[version])
        argv = ["npx", "--yes", package, "flow", "list"]
        for flow_id in ids:
            argv.extend(["--id", flow_id])
        argv.extend(["--version", version, "--state-code", "100", "--all", "--json"])
        completed = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            check=False,
            env=dict(environment),
            timeout=120,
        )
        combined_output = f"{completed.stdout}\n{completed.stderr}"
        if _secret_echo(combined_output, environment):
            raise ValueError("secret value was echoed by the exact Flow read")
        rows = []
        if completed.returncode == 0:
            try:
                rows = _flow_rows(json.loads(completed.stdout))
            except (json.JSONDecodeError, ValueError):
                rows = []
        for row in rows:
            flow_id = str(row.get("flow_id", row.get("id", ""))).lower()
            row_version = str(
                row.get("resolved_version", row.get("version", ""))
            )
            exact = f"{flow_id}@{row_version}"
            if flow_id in ids and row_version == version and exact not in observed_rows:
                observed_rows[exact] = row
        group_reports.append(
            {
                "version": version,
                "requested_flow_count": len(ids),
                "returncode": completed.returncode,
                "response_row_count": len(rows),
            }
        )

    requested_refs = {str(row["exact_ref"]) for row in requested}
    resolved_refs = sorted(requested_refs & set(observed_rows))
    missing_refs = sorted(requested_refs - set(observed_rows))
    resolved_rows = [observed_rows[exact] for exact in resolved_refs]
    raw_binding = _write_json(
        output_dir / "exact-flow-rows.json",
        {"package_spec": package, "rows": resolved_rows},
    )
    validation_count = 0
    validation_returncode = None
    validation_counts: JsonObject = {}
    if resolved_rows:
        validation_input = _write_json(
            output_dir / "validation-input.json", [row["flow"] for row in resolved_rows]
        )
        validation_dir = output_dir / "validation"
        argv = [
            "npx",
            "--yes",
            package,
            "dataset",
            "validate",
            "--input",
            str((output_dir / validation_input["path"]).resolve()),
            "--type",
            "flow",
            "--out-dir",
            str(validation_dir.resolve()),
            "--json",
        ]
        validation_environment = dict(environment)
        for name in REQUIRED_ENVIRONMENT_NAMES:
            validation_environment.pop(name, None)
        validation = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            check=False,
            env=validation_environment,
            timeout=120,
        )
        validation_count = 1
        validation_returncode = validation.returncode
        try:
            validation_payload = json.loads(validation.stdout)
        except json.JSONDecodeError:
            validation_payload = {}
        if isinstance(validation_payload, dict) and isinstance(
            validation_payload.get("counts"), dict
        ):
            validation_counts = dict(validation_payload["counts"])

    invalid_count = validation_counts.get("invalid", 0)
    complete = not missing_refs and validation_returncode == 0 and invalid_count == 0
    report = {
        "contract": EXACT_FLOW_BATCH_CONTRACT,
        "status": "exact_flow_batch_captured" if complete else "exact_flow_batch_incomplete",
        "package_spec": package,
        "requested_exact_flows": requested,
        "requested_exact_flow_count": len(requested),
        "resolved_exact_flow_count": len(resolved_refs),
        "resolved_exact_flows": resolved_refs,
        "missing_exact_flow_count": len(missing_refs),
        "missing_exact_flows": missing_refs,
        "group_reports": group_reports,
        "remote_read_executed_count": len(group_reports),
        "remote_write_executed_count": 0,
        "raw_flow_rows": raw_binding,
        "native_validation_command_count": validation_count,
        "native_validation_returncode": validation_returncode,
        "native_validation_valid_count": validation_counts.get("valid", 0),
        "native_validation_invalid_count": invalid_count,
        "next_route": (
            "bind-functional-unit-and-provider-flow-metadata"
            if complete
            else "evaluate-next-public-result-root-batch"
        ),
    }
    _write_json(output_dir / "capture-report.json", report)
    return report


def _safe_member_name(info: zipfile.ZipInfo) -> Optional[str]:
    token = info.filename.replace("\\", "/")
    path = PurePosixPath(token)
    if path.is_absolute() or token.startswith("/") or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ValueError(f"unsafe TIDAS ZIP member: {info.filename}")
    mode = info.external_attr >> 16
    if mode and stat.S_ISLNK(mode):
        raise ValueError(f"TIDAS ZIP symlink member is forbidden: {info.filename}")
    if info.is_dir():
        return None
    return str(path)


def _dataset_file_rows(path: Path) -> Optional[Tuple[str, List[JsonObject]]]:
    try:
        if path.suffix.lower() == ".jsonl":
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        elif path.suffix.lower() == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
            rows = value if isinstance(value, list) else [value]
        else:
            return None
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not rows or any(not isinstance(row, dict) for row in rows):
        return None
    kinds = {
        _SUPPORTED_ROOTS[root]
        for row in rows
        for root in _SUPPORTED_ROOTS
        if root in row
    }
    if len(kinds) != 1 or any(
        sum(root in row for root in _SUPPORTED_ROOTS) != 1 for row in rows
    ):
        return None
    return kinds.pop(), [dict(row) for row in rows]
def inspect_official_tidas_export(
    *,
    package_spec: str,
    export_path: Path,
    output_dir: Path,
) -> JsonObject:
    """Inspect an immutable user-exported official TIDAS ZIP and validate rows."""

    package = _exact_package_spec(package_spec)
    export_path = Path(export_path)
    output_dir = Path(output_dir)
    if not export_path.is_file() or not zipfile.is_zipfile(export_path):
        raise ValueError("export_path must be an official TIDAS ZIP file")
    original = export_path.read_bytes()
    export_sha256 = _sha256(original)
    extracted_relative = Path("exports") / export_sha256[:16]
    extracted_root = output_dir / extracted_relative

    members: List[Tuple[zipfile.ZipInfo, str]] = []
    seen = set()
    with zipfile.ZipFile(export_path) as archive:
        for info in archive.infolist():
            normalized = _safe_member_name(info)
            if normalized is None:
                continue
            if normalized in seen:
                raise ValueError(f"duplicate TIDAS ZIP member: {normalized}")
            seen.add(normalized)
            members.append((info, normalized))
        for info, normalized in members:
            content = archive.read(info)
            destination = extracted_root / normalized
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and destination.read_bytes() != content:
                raise ValueError(f"content-addressed extraction drifted: {normalized}")
            destination.write_bytes(content)

    grouped_rows: Dict[str, List[Tuple[str, Path, List[JsonObject]]]] = {}
    for _, normalized in sorted(members, key=lambda item: item[1]):
        path = extracted_root / normalized
        dataset_rows = _dataset_file_rows(path)
        if dataset_rows is None:
            continue
        dataset_type, rows = dataset_rows
        grouped_rows.setdefault(dataset_type, []).append((normalized, path, rows))

    validations = []
    for dataset_type, batch in sorted(grouped_rows.items()):
        combined_rows = [row for _, _, rows in batch for row in rows]
        combined_content = _json_bytes(combined_rows)
        input_relative = Path("validation-inputs") / (
            f"{dataset_type}-{_sha256(combined_content)[:12]}.json"
        )
        input_path = output_dir / input_relative
        input_binding = _write_json(input_path, combined_rows)
        validation_dir = output_dir / "validation" / (
            f"{dataset_type}-{_sha256(combined_content)[:12]}"
        )
        argv = [
            "npx",
            "--yes",
            package,
            "dataset",
            "validate",
            "--input",
            str(input_path.resolve()),
            "--type",
            dataset_type,
            "--out-dir",
            str(validation_dir.resolve()),
            "--json",
        ]
        completed = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            native_output = json.loads(completed.stdout) if completed.stdout.strip() else {}
        except json.JSONDecodeError as error:
            raise ValueError("native validation did not return JSON") from error
        validation_dir.mkdir(parents=True, exist_ok=True)
        output_binding = _write_json(validation_dir / "stdout.json", native_output)
        validations.append(
            {
                "dataset_type": dataset_type,
                "row_count": len(combined_rows),
                "members": [
                    {
                        "member": normalized,
                        "member_sha256": _sha256(path.read_bytes()),
                        "member_bytes": path.stat().st_size,
                        "row_count": len(rows),
                    }
                    for normalized, path, rows in batch
                ],
                "validation_input": {
                    "path": str(input_relative),
                    "sha256": input_binding["sha256"],
                    "bytes": input_binding["bytes"],
                },
                "argv": argv,
                "returncode": completed.returncode,
                "native_validation_passed": completed.returncode == 0,
                "native_output": {
                    "path": str(
                        (Path("validation") / validation_dir.name / output_binding["path"])
                    ),
                    "sha256": output_binding["sha256"],
                    "bytes": output_binding["bytes"],
                },
            }
        )

    if not validations:
        raise ValueError("official TIDAS ZIP contains no supported dataset rows")
    failure_count = sum(
        not row["native_validation_passed"] for row in validations
    )
    accepted = failure_count == 0
    report: JsonObject = {
        "contract": TIDAS_EXPORT_INSPECTION_CONTRACT,
        "status": (
            "official_tidas_export_inspected"
            if accepted
            else "official_tidas_export_rejected"
        ),
        "package_spec": package,
        "export_filename": export_path.name,
        "export_sha256": export_sha256,
        "export_bytes": len(original),
        "extracted_root": str(extracted_relative),
        "member_count": len(members),
        "native_validation_executed_count": sum(
            row["row_count"] for row in validations
        ),
        "native_validation_command_count": len(validations),
        "native_validation_failure_count": failure_count,
        "validations": validations,
        "remote_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "original_export_unchanged": export_path.read_bytes() == original,
        "next_route": (
            "select-tiangong-open-data-case"
            if accepted
            else "evaluate-next-tiangong-open-data-candidate"
        ),
    }
    _write_json(output_dir / "capture-report.json", report)
    return report
