"""Sanitize and pin one observed TianGong read-only capability surface."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Mapping
from urllib.parse import urlparse


JsonObject = Dict[str, object]

CAPABILITY_OBSERVATION_CONTRACT = (
    "lca_research_kernel.tiangong_capability_observation/v1"
)
CAPABILITY_ADOPTION_CONTRACT = (
    "lca_research_kernel.tiangong_capability_adoption/v1"
)
_ADOPTION_STATUS = "adopted_read_only_surface"
_HISTORICAL_PACKAGE_SPEC = "@tiangong-lca/cli@0.0.25"
_ALLOWED_RESULT_MODES = {
    "official_calculation_bundle",
    "official_api_result",
    "official_tidas_zip_export",
}
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
_LATEST_TOKEN = re.compile(r"(^|[@/:?=&._-])latest($|[@/:?=&._-])", re.IGNORECASE)
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "authorization_header",
    "cookie",
    "password",
    "secret",
    "secret_value",
    "token",
    "value_preview",
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return copy.deepcopy(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _sha256(value: object, label: str) -> str:
    token = _text(value, label)
    if not _HEX_SHA256.fullmatch(token):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return token


def _utc_timestamp(value: object, label: str) -> str:
    token = _text(value, label)
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be a valid UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{label} must have UTC offset zero")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _reject_secret_like_keys(value: object, path: str = "observation") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalized_key(key)
            if normalized in _SECRET_KEYS or any(
                normalized.endswith(f"_{token}") for token in _SECRET_KEYS
            ):
                raise ValueError(f"secret-like key is forbidden at {path}.{key}")
            _reject_secret_like_keys(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_secret_like_keys(child, f"{path}[{index}]")


def _reject_latest(value: object, path: str = "observation") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _reject_latest(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_latest(child, f"{path}[{index}]")
    elif isinstance(value, str) and _LATEST_TOKEN.search(value.strip()):
        raise ValueError(f"floating latest is forbidden at {path}")


def _argv(value: object, label: str) -> List[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty argv array")
    return [_text(part, f"{label}[{index}]") for index, part in enumerate(value)]


def _normalize_cli_observation(
    value: object,
    label: str,
    expected_argv: List[str],
) -> JsonObject:
    row = _object(value, label)
    argv = _argv(row.get("argv"), f"{label}.argv")
    if argv != expected_argv:
        raise ValueError(f"{label}.argv does not bind the exact package observation")
    exit_code = row.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise ValueError(f"{label}.exit_code must be an integer")
    result: JsonObject = {
        "argv": argv,
        "exit_code": exit_code,
        "sha256": _sha256(row.get("sha256"), f"{label}.sha256"),
    }
    if label.endswith("doctor"):
        if not isinstance(row.get("ok"), bool):
            raise ValueError(f"{label}.ok must be boolean")
        result["ok"] = row["ok"]
    return result


def _normalize_commands(value: object) -> List[JsonObject]:
    if not isinstance(value, list) or not value:
        raise ValueError("observed_commands must be a non-empty array")
    rows = []
    seen_names = set()
    for index, item in enumerate(value):
        row = _object(item, f"observed_commands[{index}]")
        name = _text(row.get("name"), f"observed_commands[{index}].name")
        if name in seen_names:
            raise ValueError(f"duplicate observed command: {name}")
        seen_names.add(name)
        availability = _text(
            row.get("availability"), f"observed_commands[{index}].availability"
        )
        if availability not in {"implemented", "planned_not_implemented"}:
            raise ValueError("observed command availability is unsupported")
        rows.append(
            {
                "name": name,
                "argv_suffix": _argv(
                    row.get("argv_suffix"),
                    f"observed_commands[{index}].argv_suffix",
                ),
                "capability_kind": _text(
                    row.get("capability_kind"),
                    f"observed_commands[{index}].capability_kind",
                ),
                "availability": availability,
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            str(row["name"]),
            json.dumps(row["argv_suffix"], separators=(",", ":")),
        ),
    )


def _normalize_documents(value: object) -> List[JsonObject]:
    if not isinstance(value, list) or not value:
        raise ValueError("official_documents must be a non-empty array")
    rows = []
    seen_roles = set()
    for index, item in enumerate(value):
        row = _object(item, f"official_documents[{index}]")
        role = _text(row.get("role"), f"official_documents[{index}].role")
        if role in seen_roles:
            raise ValueError(f"duplicate official document role: {role}")
        seen_roles.add(role)
        url = _text(row.get("url"), f"official_documents[{index}].url")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "docs.tiangong.earth":
            raise ValueError("official document URL must use docs.tiangong.earth HTTPS")
        rows.append(
            {
                "role": role,
                "url": url,
                "sha256": _sha256(
                    row.get("sha256"), f"official_documents[{index}].sha256"
                ),
                "retrieval_route": _text(
                    row.get("retrieval_route"),
                    f"official_documents[{index}].retrieval_route",
                ),
            }
        )
    return sorted(rows, key=lambda row: (str(row["role"]), str(row["url"])))


def _environment_names(value: object, label: str) -> List[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    names = []
    for index, item in enumerate(value):
        name = _text(item, f"{label}[{index}]")
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(f"{label}[{index}] is not an environment variable name")
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError(f"{label} contains duplicates")
    return sorted(names)


def _adoption_content_sha256(adoption: Mapping[str, object]) -> str:
    body = _object(adoption, "adoption")
    body.pop("adoption_id", None)
    body.pop("adoption_content_sha256", None)
    return _digest(body)


def compile_tiangong_capability_adoption(
    observation: Mapping[str, object],
) -> JsonObject:
    """Compile one exact read-only adoption from a non-secret observation."""

    raw = _object(observation, "observation")
    _reject_secret_like_keys(raw)
    _reject_latest(raw)
    if raw.get("contract") != CAPABILITY_OBSERVATION_CONTRACT:
        raise ValueError("capability observation contract is unsupported")
    if raw.get("historical_runtime_rewritten") is not False:
        raise ValueError("historical TianGong runtime must not be rewritten")
    if raw.get("remote_write_authorized") is not False:
        raise ValueError("TianGong remote write must remain unauthorized")
    if raw.get("credential_values_recorded") is not False:
        raise ValueError("credential values must not be recorded")
    historical_spec = _text(
        raw.get("historical_package_spec"), "historical_package_spec"
    )
    if historical_spec != _HISTORICAL_PACKAGE_SPEC:
        raise ValueError("historical TianGong package identity must remain 0.0.25")

    candidate = _object(raw.get("candidate"), "candidate")
    version = _text(candidate.get("version"), "candidate.version")
    if not _VERSION.fullmatch(version):
        raise ValueError("candidate.version must be an exact semantic version")
    package_spec = _text(candidate.get("package_spec"), "candidate.package_spec")
    if package_spec != f"@tiangong-lca/cli@{version}":
        raise ValueError("candidate.package_spec does not bind candidate.version")
    integrity = _text(candidate.get("npm_integrity"), "candidate.npm_integrity")
    if not integrity.startswith("sha512-") or len(integrity) <= len("sha512-"):
        raise ValueError("candidate.npm_integrity must be npm SHA-512 SRI")
    tarball_url = _text(candidate.get("tarball_url"), "candidate.tarball_url")
    expected_tarball = (
        f"https://registry.npmjs.org/@tiangong-lca/cli/-/cli-{version}.tgz"
    )
    if tarball_url != expected_tarball:
        raise ValueError("candidate.tarball_url does not bind candidate.version")
    candidate_core: JsonObject = {
        "version": version,
        "package_spec": package_spec,
        "npm_integrity": integrity,
        "tarball_url": tarball_url,
        "node_range": _text(candidate.get("node_range"), "candidate.node_range"),
        "observed_node_version": _text(
            candidate.get("observed_node_version"), "candidate.observed_node_version"
        ),
        "observed_npm_version": _text(
            candidate.get("observed_npm_version"), "candidate.observed_npm_version"
        ),
    }

    cli = _object(raw.get("cli_observations"), "cli_observations")
    cli_observations = {
        "help": _normalize_cli_observation(
            cli.get("help"),
            "cli_observations.help",
            ["npx", "--yes", package_spec, "--help"],
        ),
        "doctor": _normalize_cli_observation(
            cli.get("doctor"),
            "cli_observations.doctor",
            ["npx", "--yes", package_spec, "doctor", "--json"],
        ),
    }
    commands = _normalize_commands(raw.get("observed_commands"))
    direct_lcia = raw.get("direct_lcia_command_available")
    if not isinstance(direct_lcia, bool):
        raise ValueError("direct_lcia_command_available must be boolean")
    direct_observed = any(
        command["capability_kind"] == "direct_lcia_calculation"
        and command["availability"] == "implemented"
        for command in commands
    )
    if direct_lcia and not direct_observed:
        raise ValueError("direct LCIA availability must have a direct implemented observation")
    if not direct_lcia and direct_observed:
        raise ValueError("direct LCIA observation conflicts with availability marker")

    modes_value = raw.get("accepted_native_result_modes")
    if not isinstance(modes_value, list) or not modes_value:
        raise ValueError("accepted_native_result_modes must be a non-empty array")
    modes = sorted(
        {_text(mode, "accepted_native_result_modes item") for mode in modes_value}
    )
    if len(modes) != len(modes_value) or not set(modes).issubset(_ALLOWED_RESULT_MODES):
        raise ValueError("accepted result mode is not a TianGong official origin")

    required_names = _environment_names(
        raw.get("required_environment_names"), "required_environment_names"
    )
    present_names = _environment_names(
        raw.get("present_environment_names"), "present_environment_names"
    )
    if not set(present_names).issubset(set(required_names)):
        raise ValueError("present environment names must be required names")
    missing_names = sorted(set(required_names) - set(present_names))
    documents = _normalize_documents(raw.get("official_documents"))

    normalized_observation: JsonObject = {
        "contract": CAPABILITY_OBSERVATION_CONTRACT,
        "observed_at_utc": _utc_timestamp(
            raw.get("observed_at_utc"), "observed_at_utc"
        ),
        "historical_package_spec": historical_spec,
        "candidate": candidate_core,
        "cli_observations": cli_observations,
        "required_environment_names": required_names,
        "present_environment_names": present_names,
        "observed_commands": commands,
        "direct_lcia_command_available": direct_lcia,
        "accepted_native_result_modes": modes,
        "official_documents": documents,
        "historical_runtime_rewritten": False,
        "remote_write_authorized": False,
        "credential_values_recorded": False,
    }
    observation_sha256 = _digest(normalized_observation)
    body: JsonObject = {
        "contract": CAPABILITY_ADOPTION_CONTRACT,
        "status": _ADOPTION_STATUS,
        "observed_at_utc": normalized_observation["observed_at_utc"],
        "historical_package_spec": historical_spec,
        "current_package_spec": package_spec,
        "current_version": version,
        "npm_integrity": integrity,
        "tarball_url": tarball_url,
        "node_range": candidate_core["node_range"],
        "observed_node_version": candidate_core["observed_node_version"],
        "observed_npm_version": candidate_core["observed_npm_version"],
        "cli_observations": cli_observations,
        "observed_commands": commands,
        "direct_lcia_command_available": direct_lcia,
        "accepted_native_result_modes": modes,
        "required_environment_names": required_names,
        "present_environment_names": present_names,
        "missing_environment_names": missing_names,
        "credential_status": "available" if not missing_names else "credentials_required",
        "official_documents": documents,
        "observation_sha256": observation_sha256,
        "historical_runtime_rewritten": False,
        "remote_write_authorized": False,
        "credential_values_recorded": False,
    }
    digest = _digest(body)
    adoption = copy.deepcopy(body)
    adoption["adoption_id"] = digest[:16]
    adoption["adoption_content_sha256"] = digest
    return adoption


def verify_tiangong_capability_adoption(
    adoption: Mapping[str, object],
) -> JsonObject:
    """Verify a capability adoption's content identity and canonical semantics."""

    row = _object(adoption, "adoption")
    if row.get("contract") != CAPABILITY_ADOPTION_CONTRACT:
        raise ValueError("capability adoption contract is unsupported")
    if row.get("status") != _ADOPTION_STATUS:
        raise ValueError("capability adoption status is unsupported")
    _reject_secret_like_keys(row)
    _reject_latest(row)
    recorded_id = _text(row.get("adoption_id"), "adoption.adoption_id")
    recorded_digest = _sha256(
        row.get("adoption_content_sha256"), "adoption.adoption_content_sha256"
    )
    expected_digest = _adoption_content_sha256(row)
    if recorded_digest != expected_digest:
        raise ValueError("adoption content digest does not match governed content")
    if recorded_id != expected_digest[:16]:
        raise ValueError("adoption_id does not match content digest")

    candidate = {
        "version": row.get("current_version"),
        "package_spec": row.get("current_package_spec"),
        "npm_integrity": row.get("npm_integrity"),
        "tarball_url": row.get("tarball_url"),
        "node_range": row.get("node_range"),
        "observed_node_version": row.get("observed_node_version"),
        "observed_npm_version": row.get("observed_npm_version"),
    }
    observation = {
        "contract": CAPABILITY_OBSERVATION_CONTRACT,
        "observed_at_utc": row.get("observed_at_utc"),
        "historical_package_spec": row.get("historical_package_spec"),
        "candidate": candidate,
        "cli_observations": row.get("cli_observations"),
        "required_environment_names": row.get("required_environment_names"),
        "present_environment_names": row.get("present_environment_names"),
        "observed_commands": row.get("observed_commands"),
        "direct_lcia_command_available": row.get("direct_lcia_command_available"),
        "accepted_native_result_modes": row.get("accepted_native_result_modes"),
        "official_documents": row.get("official_documents"),
        "historical_runtime_rewritten": row.get("historical_runtime_rewritten"),
        "remote_write_authorized": row.get("remote_write_authorized"),
        "credential_values_recorded": row.get("credential_values_recorded"),
    }
    canonical = compile_tiangong_capability_adoption(observation)
    if canonical != row:
        raise ValueError("capability adoption is not in canonical form")
    return copy.deepcopy(row)
