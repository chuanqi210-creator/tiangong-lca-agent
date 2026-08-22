"""Credential-free clean-context acceptance for a frozen benchmark portfolio."""

from __future__ import annotations

import ast
import copy
import hashlib
import http.client
import importlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
import unicodedata
import urllib.request
from collections.abc import MutableMapping
from pathlib import Path
from typing import Dict, Iterable, Iterator, Mapping, Optional, Sequence, Set, Tuple


JsonObject = Dict[str, object]
ACCEPTANCE_CONTRACT = (
    "lca_research_kernel.research_council_benchmark_portfolio_acceptance/v1"
)
_MANIFEST_CONTRACT = (
    "lca_research_kernel.research_council_benchmark_portfolio_manifest/v4"
)
_PORTFOLIO_CONTRACT = (
    "lca_research_kernel.research_council_benchmark_portfolio/v1"
)
_REPLAY_CONTRACT = (
    "lca_research_kernel.research_council_benchmark_portfolio_replay/v4"
)
_ZERO_EFFECTS = {
    "remote_write_executed_count": 0,
    "shared_write_executed_count": 0,
    "calculation_trigger_executed_count": 0,
    "publication_executed_count": 0,
    "review_submission_executed_count": 0,
    "contact_executed_count": 0,
    "payment_executed_count": 0,
    "role_administration_executed_count": 0,
    "external_message_executed_count": 0,
    "unofficial_tiangong_correction_count": 0,
    "unsupported_strong_public_claim_count": 0,
    "secret_persistence_count": 0,
}
_MANIFEST_KEYS = {
    "contract",
    "workflow_runtime",
    "runtime_closure",
    "portfolio_id",
    "portfolio_content_sha256",
    "portfolio_status",
    "council_disposition",
    "freeze_proposal_id",
    "portfolio_lineage",
    "deliverables",
    "authority_source_refs",
    "file_count",
    "all_prohibited_effects_zero",
    "next_route",
    "manifest_content_sha256",
    "manifest_id",
}
_RUNTIME_DESCRIPTOR_KEYS = {"role", "source_path", "path", "sha256", "bytes"}
_DESCRIPTOR_KEYS = {"path", "sha256", "bytes"}
_TRANSITIVE_ENTRYPOINTS = {
    "lca_research_kernel/research_council_benchmark_portfolio.py",
    "lca_research_kernel/research_council_benchmark_portfolio_workflow.py",
}
_BEHAVIOR_HASH_ONLY = {
    "lca_research_kernel/project.py",
    "lca_research_kernel/research_council_workflow.py",
}
_SECRET_KEYS = {
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "password",
    "credential",
    "credentials",
    "authorization",
    "cookie",
    "cookies",
    "client_secret",
    "private_key",
    "token",
    "auth_token",
    "bearer_token",
    "session_token",
}
_ACTOR_KEYS = {
    "actor_id",
    "user_id",
    "team_id",
    "subject_id",
    "account_id",
    "principal_id",
    "live_actor",
    "authenticated_actor",
    "authenticated_user",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/]{16,}={0,2}(?:\s|$)"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\bsb_(?:publishable|secret)_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
)
_RAW_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/]{16,}={0,2}(?:\s|$)"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\bsb_(?:publishable|secret)_[A-Za-z0-9_-]{12,}\b"),
    re.compile(
        r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----\s+"
        r"[A-Za-z0-9+/=\r\n]{32,}\s+"
        r"-----END (?:RSA |EC )?PRIVATE KEY-----"
    ),
)
_RAW_ACTOR_VALUE_PATTERNS = (
    re.compile(
        r"(?im)\b(?:"
        r"authenticatedActor(?:Id)?|authenticatedUser(?:Id)?|"
        r"liveActor(?:Id)?|"
        r"actorId|userId|teamId|subjectId|accountId|principalId|"
        r"(?:actor|user|team|subject|account|principal)\.id|"
        r"authenticated[ _-]+(?:actor|user)(?:[ _-]+id)?|"
        r"live[ _-]+actor(?:[ _-]+id)?|"
        r"(?:actor|user|team|subject|account|principal)[ _-]+id"
        r")[`\"']?\s*(?::=|:|=|->|=>|—)\s*[`\"']?"
        r"[A-Za-z0-9][A-Za-z0-9@._:/+\-]{2,}[`\"']?"
    ),
)
_RAW_LABELED_SECRET_ASSIGNMENT_PATTERNS = (
    re.compile(
        r"(?im)(?<![A-Za-z0-9_])[`\"']?(?:"
        r"access[ _-]?token|refresh[ _-]?token|"
        r"auth[ _-]?token|bearer[ _-]?token|session[ _-]?token|"
        r"api[ _-]?key|apikey|client[ _-]?secret|password|cookies?|"
        r"credentials?|authorization|token|private[ _-]?key"
        r")[`\"']?\s*(?::=|:|=|->|=>|—)\s*(?:"
        r"(?P<quote>[`\"'])(?P<quoted>[^\r\n]{1,512})(?P=quote)|"
        r"(?P<bare>[A-Za-z0-9][A-Za-z0-9._~+/=@:%-]{0,511})"
        r")"
    ),
)
_RAW_SECRET_PLACEHOLDERS = {
    "dummy",
    "environment_only",
    "env_only",
    "example",
    "forbidden",
    "masked",
    "missing",
    "n_a",
    "na",
    "nil",
    "none",
    "not_applicable",
    "not_available",
    "not_set",
    "null",
    "omitted",
    "placeholder",
    "prohibited",
    "redacted",
    "test",
    "unavailable",
    "unset",
    "xxxxx",
}
_CREDENTIAL_ENV_MARKERS = (
    "TOKEN",
    "KEY",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "AUTH",
    "COOKIE",
    "SESSION",
    "PROXY",
    "SUPABASE",
    "TIANGONG",
    "CLOUD",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REPO_ROOT = Path(__file__).resolve().parents[1]


class AcceptanceError(ValueError):
    """A sanitized, stable clean-acceptance failure code."""


def _fail(code: str) -> None:
    raise AcceptanceError(code)


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise AcceptanceError("NONCANONICAL_JSON") from error


def _pretty_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise AcceptanceError("NONCANONICAL_JSON") from error


def _safe_relative(value: object) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
    ):
        _fail("UNSAFE_RELATIVE_PATH")
    relative = Path(value)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        _fail("UNSAFE_RELATIVE_PATH")
    return relative


def _absolute_nofollow(
    value: Path, *, allow_missing_tail: bool, label: str
) -> Path:
    absolute = Path(os.path.abspath(os.fspath(value)))
    cursor = Path(absolute.anchor)
    missing = False
    for part in absolute.parts[1:]:
        cursor = cursor / part
        if missing:
            continue
        try:
            status = os.lstat(cursor)
        except FileNotFoundError:
            if not allow_missing_tail:
                _fail(f"{label}_MISSING")
            missing = True
            continue
        if stat.S_ISLNK(status.st_mode):
            _fail("SYMLINK_FORBIDDEN")
    return absolute


def _secure_mkdirs(path: Path) -> None:
    absolute = _absolute_nofollow(
        path, allow_missing_tail=True, label="OUTPUT_PARENT"
    )
    pending = []
    cursor = absolute
    while not cursor.exists():
        pending.append(cursor)
        cursor = cursor.parent
    if stat.S_ISLNK(os.lstat(cursor).st_mode):
        _fail("SYMLINK_FORBIDDEN")
    for directory in reversed(pending):
        try:
            os.mkdir(directory, 0o700)
        except FileExistsError:
            _fail("OUTPUT_RACE")
        os.chmod(directory, 0o700)


def _write_exclusive(path: Path, content: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)


def _read_json_bytes(content: bytes, code: str) -> JsonObject:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AcceptanceError(code) from error
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _fail(code)
    return value


def _descriptor_bytes(content: bytes, path: str) -> JsonObject:
    return {
        "path": path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _walk_tree(root: Path) -> Tuple[Set[str], Set[str]]:
    files: Set[str] = set()
    directories: Set[str] = {"."}

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as error:
            raise AcceptanceError("DECLARED_TREE_UNREADABLE") from error
        for entry in entries:
            relative = Path(entry.path).relative_to(root).as_posix()
            try:
                status = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise AcceptanceError("DECLARED_TREE_UNREADABLE") from error
            if stat.S_ISLNK(status.st_mode):
                _fail("SYMLINK_FORBIDDEN")
            if stat.S_ISDIR(status.st_mode):
                if stat.S_IMODE(status.st_mode) != 0o700:
                    _fail("UNSAFE_DIRECTORY_MODE")
                directories.add(relative)
                visit(Path(entry.path))
            elif stat.S_ISREG(status.st_mode):
                if stat.S_IMODE(status.st_mode) != 0o600:
                    _fail("UNSAFE_FILE_MODE")
                files.add(relative)
            else:
                _fail("SPECIAL_FILE_FORBIDDEN")

    root_status = os.lstat(root)
    if (
        not stat.S_ISDIR(root_status.st_mode)
        or stat.S_IMODE(root_status.st_mode) != 0o700
    ):
        _fail("UNSAFE_DIRECTORY_MODE")
    visit(root)
    return files, directories


def _physical_tree_hash(
    root: Path, files: Iterable[str], directories: Iterable[str]
) -> str:
    rows = []
    for relative in sorted(directories):
        path = root if relative == "." else root / relative
        status = os.lstat(path)
        rows.append(
            {
                "kind": "directory",
                "path": relative,
                "mode": stat.S_IMODE(status.st_mode),
            }
        )
    for relative in sorted(files):
        path = root / relative
        content = path.read_bytes()
        rows.append(
            {
                "kind": "file",
                "path": relative,
                "mode": stat.S_IMODE(os.lstat(path).st_mode),
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    return hashlib.sha256(_canonical_bytes(rows)).hexdigest()


def _walk_security(
    value: object, path: str = "$", *, _parent_label: Optional[str] = None
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalized_security_key(key)
            if normalized == "id" and _parent_label in {
                "actor",
                "user",
                "team",
                "subject",
                "account",
                "principal",
                "authenticated_actor",
                "authenticated_user",
                "live_actor",
            }:
                _fail("LIVE_ACTOR_IDENTIFIER")
            if normalized in _SECRET_KEYS or normalized.endswith(
                (
                    "_access_token",
                    "_refresh_token",
                    "_api_key",
                    "_password",
                    "_credential",
                    "_authorization",
                    "_cookie",
                    "_client_secret",
                    "_private_key",
                )
            ):
                _fail("SECRET_MATERIAL")
            if normalized in _ACTOR_KEYS or normalized.endswith(
                (
                    "_actor_id",
                    "_user_id",
                    "_team_id",
                    "_subject_id",
                    "_principal_id",
                )
            ):
                _fail("LIVE_ACTOR_IDENTIFIER")
            _walk_security(
                child, f"{path}.{key}", _parent_label=normalized
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_security(
                child, f"{path}[{index}]", _parent_label=_parent_label
            )
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
            _fail("SECRET_MATERIAL")


def _normalized_security_key(value: object) -> str:
    text = _security_label_text(str(value)).strip()
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", text)
    return text.lower()


def _security_label_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )


def _walk_raw_security(content: bytes) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AcceptanceError("DECLARED_TEXT_INVALID") from error
    if any(pattern.search(text) for pattern in _RAW_SECRET_VALUE_PATTERNS):
        _fail("SECRET_MATERIAL")
    label_text = _security_label_text(text)
    if any(pattern.search(label_text) for pattern in _RAW_ACTOR_VALUE_PATTERNS):
        _fail("LIVE_ACTOR_IDENTIFIER")
    for pattern in _RAW_LABELED_SECRET_ASSIGNMENT_PATTERNS:
        for match in pattern.finditer(label_text):
            value = match.group("quoted") or match.group("bare") or ""
            if _raw_labeled_secret_value(
                value, quoted=match.group("quoted") is not None
            ):
                _fail("SECRET_MATERIAL")


def _raw_labeled_secret_value(value: str, *, quoted: bool) -> bool:
    stripped = value.strip()
    wrapper_stripped = stripped.strip("<>()[]{} ")
    normalized = re.sub(r"[\s-]+", "_", wrapper_stripped.lower())
    if (
        not stripped
        or normalized in _RAW_SECRET_PLACEHOLDERS
        or set(stripped) <= {"*", "x", "X", "•", "-"}
        or re.fullmatch(r"(?:\$\{[^}]+\}|\{\{[^}]+\}\})", stripped)
        or stripped.lower().startswith(
            (
                "env:",
                "os.getenv(",
                "os.environ[",
                "process.env.",
                "secret://",
                "vault://",
            )
        )
    ):
        return False
    if len(stripped) < 8:
        return False
    has_letter = bool(re.search(r"[A-Za-z]", stripped))
    has_digit = bool(re.search(r"[0-9]", stripped))
    has_secret_punctuation = bool(re.search(r"[-@~+/=:%]", stripped))
    if has_letter and (has_digit or has_secret_punctuation):
        return True
    if quoted and " " not in stripped and "\t" not in stripped:
        return len(stripped) >= 12
    return bool(re.fullmatch(r"[A-Za-z]{20,}", stripped))


def _require_zero_effects(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != set(_ZERO_EFFECTS):
        _fail("EFFECTS_INVALID")
    for item in value.values():
        if isinstance(item, bool) or not isinstance(item, int) or item != 0:
            _fail("EFFECTS_INVALID")


def _manifest_identity(manifest: Mapping[str, object]) -> None:
    if set(manifest) != _MANIFEST_KEYS or manifest.get("contract") != _MANIFEST_CONTRACT:
        _fail("MANIFEST_CONTRACT_INVALID")
    body = {
        key: copy.deepcopy(child)
        for key, child in manifest.items()
        if key not in {"manifest_content_sha256", "manifest_id"}
    }
    digest = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    if (
        manifest.get("manifest_content_sha256") != digest
        or manifest.get("manifest_id") != digest[:16]
    ):
        _fail("MANIFEST_IDENTITY_INVALID")


def _transitive_runtime_paths(
    runtime_sources: Mapping[str, bytes], entrypoints: Iterable[str]
) -> Set[str]:
    pending = list(entrypoints)
    visited: Set[str] = set()
    while pending:
        relative = pending.pop()
        if relative in visited:
            continue
        content = runtime_sources.get(relative)
        if content is None:
            _fail("RUNTIME_CLOSURE_MISMATCH")
        try:
            tree = ast.parse(content.decode("utf-8"), filename=relative)
        except (UnicodeError, SyntaxError) as error:
            raise AcceptanceError("RUNTIME_SOURCE_INVALID") from error
        visited.add(relative)
        parent = Path(relative).parent
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or node.level != 1:
                continue
            if not node.module:
                _fail("UNDECLARED_RUNTIME_IMPORT")
            dependency = (parent / f"{node.module}.py").as_posix()
            if dependency not in runtime_sources:
                _fail("UNDECLARED_RUNTIME_IMPORT")
            if dependency not in visited:
                pending.append(dependency)
    return visited


def _validate_runtime_closure(
    manifest: Mapping[str, object], content_by_path: Mapping[str, bytes]
) -> str:
    raw = manifest.get("runtime_closure")
    if not isinstance(raw, list) or not raw:
        _fail("RUNTIME_CLOSURE_MISMATCH")
    rows = []
    seen_roles = set()
    seen_sources = set()
    runtime_sources: Dict[str, bytes] = {}
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != _RUNTIME_DESCRIPTOR_KEYS:
            _fail("RUNTIME_CLOSURE_MISMATCH")
        role = item.get("role")
        source_path = item.get("source_path")
        bundled_path = item.get("path")
        if not isinstance(role, str) or role in seen_roles:
            _fail("RUNTIME_CLOSURE_MISMATCH")
        source = _safe_relative(source_path).as_posix()
        path = _safe_relative(bundled_path).as_posix()
        if path != f"runtime-tree/{source}" or source in seen_sources:
            _fail("RUNTIME_CLOSURE_MISMATCH")
        content = content_by_path.get(path)
        descriptor = manifest["deliverables"].get(path)
        expected = {
            "path": path,
            "sha256": hashlib.sha256(content or b"").hexdigest(),
            "bytes": len(content or b""),
        }
        if content is None or descriptor != expected:
            _fail("RUNTIME_CLOSURE_MISMATCH")
        if item.get("sha256") != expected["sha256"] or item.get("bytes") != expected["bytes"]:
            _fail("RUNTIME_CLOSURE_MISMATCH")
        seen_roles.add(role)
        seen_sources.add(source)
        runtime_sources[source] = content
        rows.append(dict(item))
    transitive = _transitive_runtime_paths(runtime_sources, _TRANSITIVE_ENTRYPOINTS)
    if seen_sources != transitive | _BEHAVIOR_HASH_ONLY:
        _fail("RUNTIME_CLOSURE_MISMATCH")
    workflow = manifest.get("workflow_runtime")
    workflow_rows = [
        row for row in rows if row["role"] == "benchmark_portfolio_workflow"
    ]
    if len(workflow_rows) != 1 or workflow != workflow_rows[0]:
        _fail("RUNTIME_CLOSURE_MISMATCH")
    return hashlib.sha256(_canonical_bytes(sorted(rows, key=lambda row: row["role"]))).hexdigest()


def _inspect_bundle(manifest_path: Path) -> Tuple[JsonObject, Dict[str, bytes], str, str]:
    manifest_file = _absolute_nofollow(
        manifest_path, allow_missing_tail=False, label="MANIFEST"
    )
    status = os.lstat(manifest_file)
    if (
        not stat.S_ISREG(status.st_mode)
        or stat.S_IMODE(status.st_mode) != 0o600
    ):
        _fail("UNSAFE_FILE_MODE")
    bundle = manifest_file.parent
    if manifest_file.name != "manifest.json":
        _fail("MANIFEST_NAME_INVALID")
    content = manifest_file.read_bytes()
    manifest = _read_json_bytes(content, "MANIFEST_JSON_INVALID")
    _manifest_identity(manifest)
    deliverables = manifest.get("deliverables")
    if not isinstance(deliverables, Mapping) or manifest.get("file_count") != len(deliverables):
        _fail("MANIFEST_CONTRACT_INVALID")
    expected_files = {"manifest.json"}
    expected_dirs = {"."}
    for key, descriptor in deliverables.items():
        relative = _safe_relative(key)
        if (
            not isinstance(descriptor, Mapping)
            or set(descriptor) != _DESCRIPTOR_KEYS
            or descriptor.get("path") != relative.as_posix()
            or isinstance(descriptor.get("bytes"), bool)
            or not isinstance(descriptor.get("bytes"), int)
            or not isinstance(descriptor.get("sha256"), str)
            or not _SHA256.fullmatch(str(descriptor.get("sha256")))
        ):
            _fail("MANIFEST_DESCRIPTOR_INVALID")
        expected_files.add(relative.as_posix())
        expected_dirs.update(
            parent.as_posix() for parent in relative.parents if parent != Path(".")
        )
    actual_files, actual_dirs = _walk_tree(bundle)
    if actual_files != expected_files or actual_dirs != expected_dirs:
        _fail("DECLARED_TREE_MISMATCH")
    content_by_path = {"manifest.json": content}
    for relative_text, descriptor in deliverables.items():
        relative = _safe_relative(relative_text)
        path = _absolute_nofollow(
            bundle / relative, allow_missing_tail=False, label="DELIVERABLE"
        )
        if not stat.S_ISREG(os.lstat(path).st_mode):
            _fail("DECLARED_TREE_MISMATCH")
        payload = path.read_bytes()
        if (
            descriptor.get("bytes") != len(payload)
            or descriptor.get("sha256") != hashlib.sha256(payload).hexdigest()
        ):
            _fail("DELIVERABLE_DRIFT")
        content_by_path[relative.as_posix()] = payload
    for relative, payload in content_by_path.items():
        _walk_raw_security(payload)
        if relative.endswith(".json"):
            _walk_security(_read_json_bytes(payload, "DECLARED_JSON_INVALID"))
    runtime_hash = _validate_runtime_closure(manifest, content_by_path)
    tree_hash = _physical_tree_hash(bundle, actual_files, actual_dirs)
    return manifest, content_by_path, tree_hash, runtime_hash


def _validate_terminal_semantics(
    manifest: Mapping[str, object], content_by_path: Mapping[str, bytes]
) -> JsonObject:
    portfolio = _read_json_bytes(
        content_by_path.get("portfolio.json", b""), "PORTFOLIO_JSON_INVALID"
    )
    if portfolio.get("contract") != _PORTFOLIO_CONTRACT:
        _fail("PORTFOLIO_CONTRACT_INVALID")
    body = {
        key: child
        for key, child in portfolio.items()
        if key not in {"portfolio_content_sha256", "portfolio_id"}
    }
    digest = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    if portfolio.get("portfolio_content_sha256") != digest or portfolio.get("portfolio_id") != digest[:16]:
        _fail("PORTFOLIO_IDENTITY_INVALID")
    canary = portfolio.get("harness_canary")
    if (
        not isinstance(canary, Mapping)
        or canary.get("harness_valid") is not True
        or canary.get("formal_benchmark_eligible") is not False
    ):
        _fail("CANARY_FAILED")
    discovery = portfolio.get("positive_discovery")
    if not isinstance(discovery, Mapping) or discovery.get("positive_discovery_status") != "exhausted":
        _fail("DISCOVERY_NOT_EXHAUSTED")
    if discovery.get("qualification_execution_status") != "completed":
        _fail("QUALIFICATION_INCOMPLETE")
    _require_zero_effects(portfolio.get("effects"))
    status_value = portfolio.get("portfolio_status")
    admitted = discovery.get("admitted_cluster_count")
    freeze = portfolio.get("freeze_proposal")
    if status_value == "no_positive_benchmark_currently_available":
        if (
            admitted != 0
            or portfolio.get("admitted_clusters") != []
            or portfolio.get("council_disposition")
            != "insufficient_evidence_no_promotion"
            or portfolio.get("next_route")
            != "requalify-benchmark-portfolio-on-tiangong-content-change"
        ):
            _fail("TERMINAL_INCONSISTENT")
        if freeze is not None or "freeze-proposal.json" in content_by_path:
            _fail("FORGED_FREEZE")
        terminal_route = "requalify-benchmark-portfolio-on-tiangong-content-change"
    elif status_value == "benchmark_portfolio_ready":
        if (
            isinstance(admitted, bool)
            or not isinstance(admitted, int)
            or admitted < 1
            or not isinstance(freeze, Mapping)
            or "freeze-proposal.json" not in content_by_path
            or portfolio.get("council_disposition")
            != "ready_for_separate_evaluation_goal"
            or portfolio.get("next_route")
            != "create-separate-research-council-evaluation-goal"
        ):
            _fail("TERMINAL_INCONSISTENT")
        terminal_route = "create-separate-research-council-evaluation-goal"
    else:
        _fail("TERMINAL_INCONSISTENT")
    if freeze is not None and status_value != "benchmark_portfolio_ready":
        _fail("FORGED_FREEZE")
    if portfolio.get("council_ab_executed") is not False or portfolio.get("council_promoted") is not False:
        _fail("COUNCIL_SCOPE_VIOLATION")
    if (
        manifest.get("portfolio_id") != portfolio.get("portfolio_id")
        or manifest.get("portfolio_content_sha256")
        != portfolio.get("portfolio_content_sha256")
        or manifest.get("portfolio_status") != status_value
        or manifest.get("council_disposition") != portfolio.get("council_disposition")
        or manifest.get("freeze_proposal_id")
        != (freeze.get("freeze_proposal_id") if isinstance(freeze, Mapping) else None)
        or manifest.get("all_prohibited_effects_zero") is not True
        or manifest.get("next_route") != "accept-benchmark-portfolio-read-only"
    ):
        _fail("TERMINAL_INCONSISTENT")
    replay = _read_json_bytes(
        content_by_path.get("replay.json", b""), "REPLAY_CONTRACT_INVALID"
    )
    if (
        replay.get("contract") != _REPLAY_CONTRACT
        or replay.get("portfolio_id") != portfolio.get("portfolio_id")
        or replay.get("portfolio_content_sha256")
        != portfolio.get("portfolio_content_sha256")
        or replay.get("deterministic_replay_ready") is not True
        or replay.get("network_required") is not False
        or replay.get("credential_required") is not False
        or replay.get("next_route") != "accept-benchmark-portfolio-read-only"
    ):
        _fail("REPLAY_CONTRACT_INVALID")
    _require_zero_effects(replay.get("effects"))
    validation = _read_json_bytes(
        content_by_path.get("validation.json", b""), "VALIDATION_CONTRACT_INVALID"
    )
    if (
        validation.get("status") != "passed"
        or validation.get("terminal_predicate_derived") is not True
        or validation.get("freshwater_canary_valid_nonpromotable") is not True
        or validation.get("surface_exhausted") is not True
        or validation.get("positive_qualification_completed") is not True
        or validation.get("all_prohibited_effects_zero") is not True
        or validation.get("council_ab_executed") is not False
        or validation.get("council_promoted") is not False
    ):
        _fail("VALIDATION_CONTRACT_INVALID")
    return {
        "portfolio": portfolio,
        "terminal_route": terminal_route,
        "replay": replay,
    }


class _GuardedEnviron(MutableMapping):
    def __init__(self, source: Mapping[str, str], owner: "CleanRuntimeGuards"):
        self._data = dict(source)
        self._owner = owner

    def _check(self, key: object) -> None:
        name = str(key).upper()
        if any(marker in name for marker in _CREDENTIAL_ENV_MARKERS):
            self._owner.credential_environment_read_attempt_count += 1
            raise RuntimeError("CREDENTIAL_ENV_READ_BLOCKED")

    def __getitem__(self, key):
        self._check(key)
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[str(key)] = str(value)

    def __delitem__(self, key):
        del self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key, default=None):
        self._check(key)
        return self._data.get(key, default)

    def __contains__(self, key):
        self._check(key)
        return key in self._data


class CleanRuntimeGuards:
    """Network, credential-environment, and local-file guards for clean replay."""

    def __init__(
        self,
        *,
        allowed_read_paths: Set[Path],
        allowed_read_roots: Set[Path],
        protected_local_root: Optional[Path] = None,
    ) -> None:
        self.allowed_read_paths = {path.resolve() for path in allowed_read_paths}
        self.allowed_read_roots = {path.resolve() for path in allowed_read_roots}
        self.protected_local_root = (
            Path(protected_local_root).resolve()
            if protected_local_root is not None
            else None
        )
        self.network_attempt_count = 0
        self.credential_environment_read_attempt_count = 0
        self.undeclared_local_file_read_count = 0
        self._restores = []
        self._audit_installed = False

    def network_probe(self, *_args, **_kwargs):
        self.network_attempt_count += 1
        raise RuntimeError("NETWORK_DISABLED")

    def getenv(self, key, default=None):
        name = str(key).upper()
        if any(marker in name for marker in _CREDENTIAL_ENV_MARKERS):
            self.credential_environment_read_attempt_count += 1
            raise RuntimeError("CREDENTIAL_ENV_READ_BLOCKED")
        return self._original_environ.get(key, default)

    def _path_allowed(self, path: Path) -> bool:
        resolved = path.resolve(strict=False)
        if resolved in self.allowed_read_paths:
            return True
        return any(resolved == root or root in resolved.parents for root in self.allowed_read_roots)

    def open_for_read(self, path: Path) -> bytes:
        if not self._path_allowed(Path(path)):
            self.undeclared_local_file_read_count += 1
            raise RuntimeError("UNDECLARED_LOCAL_FILE_READ")
        return Path(path).read_bytes()

    def install_audit_hook(self) -> None:
        if self._audit_installed:
            return

        def audit(event, args):
            if event != "open" or not args:
                return
            raw = args[0]
            if isinstance(raw, int) or not isinstance(raw, (str, bytes, os.PathLike)):
                return
            try:
                path = Path(os.fsdecode(raw)).resolve(strict=False)
            except (OSError, TypeError, ValueError):
                return
            root = self.protected_local_root
            if root is None or not (path == root or root in path.parents):
                return
            if not self._path_allowed(path):
                self.undeclared_local_file_read_count += 1
                raise RuntimeError("UNDECLARED_LOCAL_FILE_READ")

        sys.addaudithook(audit)
        self._audit_installed = True

    def __enter__(self):
        self._original_environ = os.environ
        guarded = _GuardedEnviron(dict(os.environ), self)
        replacements = [
            (socket, "create_connection", self.network_probe),
            (socket, "getaddrinfo", self.network_probe),
            (urllib.request, "urlopen", self.network_probe),
            (http.client.HTTPConnection, "connect", self.network_probe),
            (http.client.HTTPSConnection, "connect", self.network_probe),
            (os, "getenv", self.getenv),
            (os, "environ", guarded),
        ]
        def socket_connect(_socket, *args, **kwargs):
            return self.network_probe(*args, **kwargs)

        replacements.append((socket.socket, "connect", socket_connect))
        replacements.append((socket.socket, "connect_ex", socket_connect))
        replacements.append((socket.socket, "sendto", socket_connect))
        for owner, name, replacement in replacements:
            original = getattr(owner, name)
            setattr(owner, name, replacement)
            self._restores.append((owner, name, original))
        return self

    def __exit__(self, exc_type, exc, traceback):
        for owner, name, original in reversed(self._restores):
            setattr(owner, name, original)
        self._restores.clear()
        return False


def _copy_declared_tree(
    output_dir: Path, content_by_path: Mapping[str, bytes]
) -> Tuple[Path, str]:
    clean = output_dir / "clean-tree"
    os.mkdir(clean, 0o700)
    os.chmod(clean, 0o700)
    declared = clean / "declared-bundle"
    os.mkdir(declared, 0o700)
    os.chmod(declared, 0o700)
    for relative_text, content in sorted(content_by_path.items()):
        relative = _safe_relative(relative_text)
        parent = declared / relative.parent
        _secure_mkdirs(parent)
        _write_exclusive(declared / relative, content)

    runtime_root = clean / "runtime-root"
    os.mkdir(runtime_root, 0o700)
    os.chmod(runtime_root, 0o700)
    for relative_text, content in sorted(content_by_path.items()):
        source_relative: Optional[Path] = None
        relative = Path(relative_text)
        if relative.parts[:1] == ("runtime-tree",):
            source_relative = Path(*relative.parts[1:])
        elif relative.parts[:2] == ("source-tree", "authority-root"):
            source_relative = Path(*relative.parts[2:])
        elif relative.parts[:1] == ("source-tree",):
            source_relative = relative
        if source_relative is None:
            continue
        destination = runtime_root / source_relative
        _secure_mkdirs(destination.parent)
        _write_exclusive(destination, content)
    files, directories = _walk_tree(clean)
    return clean, _physical_tree_hash(clean, files, directories)


def _clean_subprocess_env() -> Dict[str, str]:
    allowed_names = {"PATH", "LANG", "LC_ALL", "TMPDIR"}
    env = {}
    for key in sorted(allowed_names):
        value = os.environ.get(key)
        if isinstance(value, str):
            env[key] = value
    env.update(
        {
            "HOME": "/nonexistent",
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


def _worker(clean_root: Path) -> JsonObject:
    clean = _absolute_nofollow(clean_root, allow_missing_tail=False, label="CLEAN_ROOT")
    declared = clean / "declared-bundle"
    runtime_root = clean / "runtime-root"
    manifest = _read_json_bytes(
        (declared / "manifest.json").read_bytes(), "MANIFEST_JSON_INVALID"
    )
    allowed_paths = {
        path.resolve()
        for path in clean.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    guards = CleanRuntimeGuards(
        allowed_read_paths=allowed_paths,
        allowed_read_roots={runtime_root},
        protected_local_root=_REPO_ROOT,
    )
    original_path = list(sys.path)
    repo = _REPO_ROOT.resolve()
    sys.path = [str(runtime_root)] + [
        value
        for value in original_path
        if value
        and not (
            Path(value).resolve(strict=False) == repo
            or repo in Path(value).resolve(strict=False).parents
        )
    ]
    try:
        guards.install_audit_hook()
        with guards:
            portfolio_module = importlib.import_module(
                "lca_research_kernel.research_council_benchmark_portfolio"
            )
            workflow_module = importlib.import_module(
                "lca_research_kernel.research_council_benchmark_portfolio_workflow"
            )
            input_path = runtime_root / "source-tree/input-manifest.json"
            expected_portfolio = _read_json_bytes(
                (declared / "portfolio.json").read_bytes(),
                "PORTFOLIO_JSON_INVALID",
            )
            first = portfolio_module.compile_benchmark_portfolio(
                input_manifest_path=input_path, authority_root=runtime_root
            )
            second = portfolio_module.compile_benchmark_portfolio(
                input_manifest_path=input_path, authority_root=runtime_root
            )
            if first != second or first != expected_portfolio:
                _fail("NONDETERMINISTIC_REPLAY")
            input_manifest = workflow_module._read_input_manifest(input_path)
            lineage = manifest.get("portfolio_lineage")
            supersedes = None
            if isinstance(lineage, Mapping) and lineage.get("supersedes_portfolio_id") is not None:
                supersedes = {
                    "portfolio_id": lineage.get("supersedes_portfolio_id"),
                    "manifest_sha256": lineage.get("supersedes_manifest_sha256"),
                }

            def regenerate():
                files = workflow_module._bundle_files(
                    first,
                    input_manifest_path=input_path,
                    input_manifest=input_manifest,
                    supersedes=supersedes,
                    authority_root=runtime_root,
                )
                generated_manifest = workflow_module._manifest(
                    first, files, supersedes=supersedes
                )
                files[Path("manifest.json")] = workflow_module.pretty_json_bytes(
                    generated_manifest
                )
                return files, generated_manifest

            files_a, manifest_a = regenerate()
            files_b, manifest_b = regenerate()
            if manifest_a != manifest_b or files_a != files_b:
                _fail("NONDETERMINISTIC_REPLAY")
            if manifest_a != manifest:
                _fail("RECOMPILED_MANIFEST_MISMATCH")
            expected_names = {"manifest.json", *manifest["deliverables"].keys()}
            if {path.as_posix() for path in files_a} != expected_names:
                _fail("RECOMPILED_TREE_MISMATCH")
            for relative, content in files_a.items():
                if (declared / relative).read_bytes() != content:
                    _fail("RECOMPILED_BYTES_MISMATCH")
        local_module_paths = set()
        undeclared_imports = 0
        for name, module in list(sys.modules.items()):
            if not name.startswith("lca_research_kernel"):
                continue
            file_name = getattr(module, "__file__", None)
            if not file_name:
                continue
            path = Path(file_name).resolve()
            local_module_paths.add(path)
            if not (path == runtime_root or runtime_root in path.parents):
                undeclared_imports += 1
        runtime_identity = hashlib.sha256(
            _canonical_bytes(
                sorted(
                    path.relative_to(runtime_root).as_posix()
                    for path in local_module_paths
                    if path == runtime_root or runtime_root in path.parents
                )
            )
        ).hexdigest()
        generated_tree_identity = hashlib.sha256(
            _canonical_bytes(
                [
                    {
                        "path": path.as_posix(),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "bytes": len(content),
                    }
                    for path, content in sorted(
                        files_a.items(), key=lambda item: item[0].as_posix()
                    )
                ]
            )
        ).hexdigest()
        return {
            "ok": True,
            "portfolio_id": first["portfolio_id"],
            "portfolio_content_sha256": first["portfolio_content_sha256"],
            "manifest_id": manifest["manifest_id"],
            "manifest_content_sha256": manifest["manifest_content_sha256"],
            "generated_bundle_tree_sha256": generated_tree_identity,
            "loaded_runtime_identity": runtime_identity,
            "network_attempt_count": guards.network_attempt_count,
            "credential_environment_read_attempt_count": (
                guards.credential_environment_read_attempt_count
            ),
            "undeclared_local_import_count": undeclared_imports,
            "undeclared_local_file_read_count": guards.undeclared_local_file_read_count,
            "credential_environment_names_present": sorted(
                key
                for key in os.environ
                if any(marker in key.upper() for marker in _CREDENTIAL_ENV_MARKERS)
            ),
        }
    finally:
        sys.path = original_path


def _run_worker(clean_root: Path) -> JsonObject:
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(Path(__file__).resolve()),
            "--worker-root",
            str(clean_root),
        ],
        cwd=clean_root,
        env=_clean_subprocess_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
    )
    try:
        response = json.loads(process.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AcceptanceError("CLEAN_SUBPROCESS_PROTOCOL_FAILED") from error
    if (
        process.returncode != 0
        or process.stderr
        or not isinstance(response, dict)
        or response.get("ok") is not True
    ):
        code = response.get("code") if isinstance(response, dict) else None
        raise AcceptanceError(
            code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]+", code) else "CLEAN_SUBPROCESS_FAILED"
        )
    for field in (
        "network_attempt_count",
        "credential_environment_read_attempt_count",
        "undeclared_local_import_count",
        "undeclared_local_file_read_count",
    ):
        if response.get(field) != 0:
            _fail("CLEAN_RUNTIME_BOUNDARY_VIOLATED")
    if response.get("credential_environment_names_present") != []:
        _fail("CREDENTIAL_ENV_PRESENT")
    return response


def accept_benchmark_portfolio(
    *, manifest_path: Path, output_dir: Path
) -> JsonObject:
    """Reopen one declared bundle and accept it only after isolated byte replay."""

    manifest_file = _absolute_nofollow(
        Path(manifest_path), allow_missing_tail=False, label="MANIFEST"
    )
    target = _absolute_nofollow(
        Path(output_dir), allow_missing_tail=True, label="OUTPUT"
    )
    if target.exists():
        _fail("OUTPUT_EXISTS")
    manifest, content_by_path, source_hash_before, runtime_hash = _inspect_bundle(
        manifest_file
    )
    semantic = _validate_terminal_semantics(manifest, content_by_path)
    _secure_mkdirs(target.parent)
    try:
        os.mkdir(target, 0o700)
    except FileExistsError as error:
        raise AcceptanceError("OUTPUT_EXISTS") from error
    os.chmod(target, 0o700)
    clean, clean_hash_before = _copy_declared_tree(target, content_by_path)
    worker = _run_worker(clean)
    clean_files, clean_dirs = _walk_tree(clean)
    clean_hash_after = _physical_tree_hash(clean, clean_files, clean_dirs)
    if clean_hash_before != clean_hash_after:
        _fail("CLEAN_TREE_CHANGED")
    try:
        _, _, source_hash_after, runtime_hash_after = _inspect_bundle(manifest_file)
    except AcceptanceError as error:
        raise AcceptanceError("SOURCE_TREE_CHANGED") from error
    if source_hash_before != source_hash_after or runtime_hash != runtime_hash_after:
        _fail("SOURCE_TREE_CHANGED")
    portfolio = semantic["portfolio"]
    gates = {
        "manifest_integrity": True,
        "declared_tree_closed": True,
        "paths_no_follow": True,
        "strict_modes": True,
        "secret_and_actor_scan": True,
        "freshwater_canary_passed_nonpromotable": True,
        "positive_discovery_exhausted": True,
        "terminal_predicate_derived": True,
        "freeze_consistent": True,
        "zero_effects": True,
        "runtime_closure_complete": True,
        "clean_subprocess_recompiled": True,
        "deterministic_replay": True,
        "network_disabled": True,
        "credential_environment_isolated": True,
        "undeclared_local_access_absent": True,
        "source_tree_unchanged": True,
        "clean_tree_unchanged": True,
    }
    body: JsonObject = {
        "contract": ACCEPTANCE_CONTRACT,
        "accepted": True,
        "portfolio_id": portfolio["portfolio_id"],
        "portfolio_content_sha256": portfolio["portfolio_content_sha256"],
        "manifest_id": manifest["manifest_id"],
        "manifest_content_sha256": manifest["manifest_content_sha256"],
        "portfolio_status": portfolio["portfolio_status"],
        "council_disposition": portfolio["council_disposition"],
        "deterministic_replay_passed": True,
        "clean_context_passed": True,
        "zero_prohibited_side_effects": True,
        "source_bundle_tree_sha256_before": source_hash_before,
        "source_bundle_tree_sha256_after": source_hash_after,
        "clean_tree_sha256_before": clean_hash_before,
        "clean_tree_sha256_after": clean_hash_after,
        "runtime_tree_sha256": runtime_hash,
        "replay_identity": {
            "generated_bundle_tree_sha256": worker[
                "generated_bundle_tree_sha256"
            ],
            "loaded_runtime_identity": worker["loaded_runtime_identity"],
        },
        "gates": gates,
        "effects": copy.deepcopy(_ZERO_EFFECTS),
        "network_attempt_count": worker["network_attempt_count"],
        "credential_environment_read_attempt_count": worker[
            "credential_environment_read_attempt_count"
        ],
        "credential_environment_names_present": worker[
            "credential_environment_names_present"
        ],
        "undeclared_local_import_count": worker["undeclared_local_import_count"],
        "undeclared_local_file_read_count": worker[
            "undeclared_local_file_read_count"
        ],
        "council_ab_executed": False,
        "council_promoted": False,
        "next_route": semantic["terminal_route"],
    }
    digest = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    result = {
        **body,
        "acceptance_content_sha256": digest,
        "acceptance_id": digest[:16],
    }
    _write_exclusive(target / "independent-acceptance.json", _pretty_bytes(result))
    return result


def _parse_public_args(argv: Sequence[str]) -> Tuple[Path, Path]:
    if len(argv) != 4 or set(argv[::2]) != {"--manifest", "--output-dir"}:
        _fail("ARGUMENT_SHAPE_INVALID")
    values = {argv[index]: argv[index + 1] for index in range(0, len(argv), 2)}
    return Path(values["--manifest"]), Path(values["--output-dir"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--worker-root":
        try:
            result = _worker(Path(args[1]))
        except AcceptanceError as error:
            result = {"ok": False, "code": str(error)}
            sys.stdout.buffer.write(_canonical_bytes(result))
            return 2
        except BaseException:
            result = {"ok": False, "code": "CLEAN_SUBPROCESS_INTERNAL_ERROR"}
            sys.stdout.buffer.write(_canonical_bytes(result))
            return 2
        sys.stdout.buffer.write(_canonical_bytes(result))
        return 0
    try:
        manifest, output = _parse_public_args(args)
        result = accept_benchmark_portfolio(
            manifest_path=manifest, output_dir=output
        )
    except AcceptanceError as error:
        sys.stdout.buffer.write(
            _canonical_bytes({"ok": False, "code": str(error)})
        )
        return 2
    sys.stdout.buffer.write(
        _canonical_bytes({"ok": True, "acceptance_id": result["acceptance_id"]})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ACCEPTANCE_CONTRACT",
    "AcceptanceError",
    "CleanRuntimeGuards",
    "accept_benchmark_portfolio",
    "main",
]
