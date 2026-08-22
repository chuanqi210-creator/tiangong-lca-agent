#!/usr/bin/env python3
"""Fail-closed checks for the generated public tree and clean release history."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_clean_public_release import (
    is_generated_public_path,
    public_source_paths,
    public_tree_sha256,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_PATTERNS = (
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "private_key",
        re.compile("-----BEGIN " + r"(?:RSA |EC |OPENSSH )?" + "PRIVATE KEY-----"),
    ),
)
_LOCAL_PATHS = (
    re.compile(r"/Users/[A-Za-z0-9._-]+(?:/|\b)"),
    re.compile(r"C:\\Users\\[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"/home/(?!runner(?:/|\b))[A-Za-z0-9._-]+(?:/|\b)"),
)
_PERSONAL_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+-]+@(?:gmail\.com|qq\.com|163\.com|126\.com|icloud\.com|outlook\.com)\b",
    re.IGNORECASE,
)
_CAPTURED_INTERNAL_URL = re.compile(
    r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?/(?:research/|v1/jobs/)",
    re.IGNORECASE,
)
_PLACEHOLDERS = {"replace-me", "example", "example.invalid", "changeme-placeholder"}
_PUBLIC_MANIFEST_KEYS = {
    "contract",
    "accepted_product_readiness",
    "private_archive",
    "d18_contracts",
    "build",
    "provider",
    "public_tree_sha256",
    "manifest_sha256",
}
_APACHE_2_0_LICENSE_SHA256 = (
    "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
)
_REQUIRED_PUBLIC_DOCUMENTS = (
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
)


def _canonical_sha256(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _public_manifest_findings(manifest: Mapping[str, object]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if set(manifest) != _PUBLIC_MANIFEST_KEYS or manifest.get("contract") != "public-release-manifest/v1":
        findings.append({"category": "public_manifest_contract_invalid", "path": "public-release-manifest.json"})
    unsigned = dict(manifest)
    claimed_manifest_sha = unsigned.pop("manifest_sha256", None)
    if claimed_manifest_sha != _canonical_sha256(unsigned):
        findings.append({"category": "public_manifest_hash_drift", "path": "public-release-manifest.json"})
    hash_paths = (
        ("accepted_product_readiness", "release_id"),
        ("accepted_product_readiness", "receipt_sha256"),
        ("d18_contracts", "top_level_architecture_sha256"),
        ("build", "runtime_release_id"),
        ("build", "backend_tree_sha256"),
        ("build", "frontend_input_sha256"),
        ("provider", "catalog_sha256"),
        ("provider", "profile_policy_sha256"),
    )
    for section, field in hash_paths:
        row = manifest.get(section)
        value = row.get(field) if isinstance(row, Mapping) else None
        if _SHA256.fullmatch(str(value)) is None:
            findings.append({"category": "public_manifest_contract_invalid", "path": f"{section}.{field}"})
    archive = manifest.get("private_archive")
    source_commit = archive.get("source_commit") if isinstance(archive, Mapping) else None
    source_tree = archive.get("source_tree_oid") if isinstance(archive, Mapping) else None
    object_format = archive.get("git_object_format") if isinstance(archive, Mapping) else None
    oid_length = 40 if object_format == "sha1" else 64 if object_format == "sha256" else 0
    if (
        oid_length == 0
        or re.fullmatch(rf"[0-9a-f]{{{oid_length}}}", str(source_commit)) is None
        or re.fullmatch(rf"[0-9a-f]{{{oid_length}}}", str(source_tree)) is None
    ):
        findings.append({"category": "public_manifest_contract_invalid", "path": "private_archive"})
    if _SHA256.fullmatch(str(manifest.get("public_tree_sha256"))) is None:
        findings.append({"category": "public_manifest_contract_invalid", "path": "public_tree_sha256"})
    return findings


def _tracked_paths(root: Path) -> set[str]:
    if not (root / ".git").exists():
        return set()
    try:
        raw = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("public release Git index is unavailable") from error
    return {
        path.decode("utf-8", "surrogateescape")
        for path in raw.split(b"\0")
        if path
    }


def _release_contract_findings(
    root: Path, tracked_paths: set[str]
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for relative in _REQUIRED_PUBLIC_DOCUMENTS:
        target = root / relative
        if not target.is_file() or target.is_symlink():
            findings.append(
                {"category": "required_public_document_missing", "path": relative}
            )
        elif tracked_paths and relative not in tracked_paths:
            findings.append(
                {"category": "required_public_document_untracked", "path": relative}
            )

    license_path = root / "LICENSE"
    if (
        license_path.is_file()
        and not license_path.is_symlink()
        and hashlib.sha256(license_path.read_bytes()).hexdigest()
        != _APACHE_2_0_LICENSE_SHA256
    ):
        findings.append(
            {"category": "selected_license_text_drift", "path": "LICENSE"}
        )

    ledger_path = root / "public-release-redistribution.json"
    if not ledger_path.is_file() or ledger_path.is_symlink():
        findings.append(
            {
                "category": "public_redistribution_ledger_missing",
                "path": "public-release-redistribution.json",
            }
        )
        return findings
    if tracked_paths and "public-release-redistribution.json" not in tracked_paths:
        findings.append(
            {
                "category": "public_redistribution_ledger_untracked",
                "path": "public-release-redistribution.json",
            }
        )
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        ledger = None
    if not isinstance(ledger, Mapping):
        findings.append(
            {
                "category": "public_redistribution_ledger_invalid",
                "path": "public-release-redistribution.json",
            }
        )
        return findings
    unsigned = dict(ledger)
    claimed_sha256 = unsigned.pop("ledger_sha256", None)
    rows = unsigned.get("rows")
    if (
        set(ledger)
        != {"contract", "rows", "unresolved_emitted_count", "ledger_sha256"}
        or unsigned.get("contract") != "d18.public_redistribution_ledger/v1"
        or rows != []
        or unsigned.get("unresolved_emitted_count") != 0
        or claimed_sha256 != _canonical_sha256(unsigned)
    ):
        findings.append(
            {
                "category": "public_redistribution_ledger_invalid",
                "path": "public-release-redistribution.json",
            }
        )
    return findings


def _tracked_source_drift(root: Path) -> bool:
    if not (root / ".git").exists():
        return False
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if head.returncode != 0:
        return False
    for arguments in (
        ("diff", "--quiet", "HEAD", "--"),
        ("diff", "--cached", "--quiet", "HEAD", "--"),
    ):
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            return True
    return False


def load_security_lock(path: Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file() or target.is_symlink():
        raise ValueError("public release security lock is unavailable")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("public release security lock is invalid") from error
    gitleaks = value.get("gitleaks") if isinstance(value, Mapping) else None
    assets = gitleaks.get("assets") if isinstance(gitleaks, Mapping) else None
    if (
        value.get("contract") != "d18.public_release_security_lock/v1"
        or not isinstance(gitleaks, Mapping)
        or not re.fullmatch(r"v\d+\.\d+\.\d+", str(gitleaks.get("tag")))
        or not re.fullmatch(r"[0-9a-f]{40}", str(gitleaks.get("commit")))
        or not isinstance(assets, Mapping)
        or not assets
    ):
        raise ValueError("public release security lock is invalid")
    for row in assets.values():
        if (
            not isinstance(row, Mapping)
            or _SHA256.fullmatch(str(row.get("sha256"))) is None
            or not str(row.get("url", "")).startswith(
                f"https://github.com/gitleaks/gitleaks/releases/download/{gitleaks['tag']}/"
            )
        ):
            raise ValueError("public release security asset lock is invalid")
    return dict(value)


def locked_asset(lock: Mapping[str, Any]) -> Mapping[str, Any]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    machine = "aarch64" if machine in {"arm64", "aarch64"} and system == "linux" else machine
    machine = "arm64" if machine in {"arm64", "aarch64"} and system == "darwin" else machine
    key = f"{system}-{machine}"
    assets = lock["gitleaks"]["assets"]
    if key not in assets:
        raise ValueError(f"no locked gitleaks asset for {key}")
    return assets[key]


def install_locked_gitleaks(
    lock: Mapping[str, Any], archive_path: Path, install_root: Path
) -> Path:
    asset = locked_asset(lock)
    archive = Path(archive_path)
    if not archive.is_file() or archive.is_symlink():
        raise ValueError("locked gitleaks archive is unavailable")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != asset["sha256"]:
        raise ValueError("locked gitleaks archive hash drifted")
    root = Path(install_root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / "gitleaks"
    if target.exists():
        raise ValueError("gitleaks installation target already exists")
    with tarfile.open(archive, "r:gz") as bundle:
        members = [member for member in bundle.getmembers() if member.name == "gitleaks"]
        if len(members) != 1 or not members[0].isfile() or members[0].size > 100_000_000:
            raise ValueError("locked gitleaks archive layout is invalid")
        source = bundle.extractfile(members[0])
        if source is None:
            raise ValueError("locked gitleaks binary is unavailable")
        payload = source.read(100_000_001)
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o700,
    )
    try:
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    version = subprocess.run(
        [str(target), "version"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ).stdout
    if str(lock["gitleaks"]["tag"]).removeprefix("v") not in version:
        raise ValueError("locked gitleaks binary version drifted")
    return target


def _text_findings(path: str, text: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for category, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            findings.append({"category": category, "path": path})
    if any(pattern.search(text) for pattern in _LOCAL_PATHS):
        findings.append({"category": "local_absolute_path", "path": path})
    if _PERSONAL_EMAIL.search(text):
        findings.append({"category": "personal_email", "path": path})
    if _CAPTURED_INTERNAL_URL.search(text):
        findings.append({"category": "captured_internal_url", "path": path})
    generic = re.finditer(
        r"(?im)^[ \t]*(?:[A-Z0-9_]*(?:API_KEY|SECRET|PASSWORD|ACCESS_TOKEN)|api[_-]?key|secret|password|access[_-]?token)\s*[:=]\s*[\"']([^\"']{8,})[\"']",
        text,
    )
    for match in generic:
        value = match.group(1).strip().lower()
        if not any(placeholder in value for placeholder in _PLACEHOLDERS):
            findings.append({"category": "credential_assignment", "path": path})
            break
    return findings


def run_gitleaks(binary: Path, root: Path) -> list[dict[str, str]]:
    executable = Path(binary)
    if not executable.is_file() or executable.is_symlink():
        raise ValueError("locked gitleaks binary is unavailable")
    with tempfile.TemporaryDirectory() as canary_directory:
        canary_root = Path(canary_directory)
        (canary_root / "canary.txt").write_text(
            'token = "ghp_' + ("a" * 36) + '"\n',
            encoding="utf-8",
        )
        canary = subprocess.run(
            [str(executable), "detect", "--source", str(canary_root), "--no-git", "--no-banner", "--exit-code", "1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if canary.returncode != 1:
            raise ValueError("locked gitleaks detection canary failed")
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "gitleaks.json"
        result = subprocess.run(
            [
                str(executable),
                "detect",
                "--source",
                str(Path(root).resolve()),
                "--no-git",
                "--no-banner",
                "--redact",
                "--report-format",
                "json",
                "--report-path",
                str(report),
                "--exit-code",
                "1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode not in {0, 1}:
            raise ValueError("locked gitleaks scan failed")
        rows = json.loads(report.read_text(encoding="utf-8")) if report.exists() else []
        if not isinstance(rows, list):
            raise ValueError("locked gitleaks report is invalid")
        findings = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            reported = Path(str(row.get("File", "")))
            try:
                display_path = reported.resolve().relative_to(Path(root).resolve()).as_posix()
            except ValueError:
                display_path = str(reported)
            findings.append(
                {
                    "category": f"gitleaks:{row.get('RuleID', 'unknown')}",
                    "path": display_path,
                }
            )
        return findings


def verify_clean_public_tree(
    staging_root: Path,
    manifest: Mapping[str, object],
    *,
    gitleaks_binary: Path | None = None,
    require_gitleaks: bool = False,
) -> dict[str, object]:
    root = Path(staging_root).resolve()
    findings: list[dict[str, str]] = []
    findings.extend(_public_manifest_findings(manifest))
    expected_tree = manifest.get("public_tree_sha256") if isinstance(manifest, Mapping) else None
    actual_tree = public_tree_sha256(root)
    if expected_tree != actual_tree:
        findings.append({"category": "public_tree_hash_drift", "path": "."})
    tracked_paths = _tracked_paths(root)
    findings.extend(_release_contract_findings(root, tracked_paths))
    if _tracked_source_drift(root):
        findings.append({"category": "tracked_source_drift", "path": "."})
    source_paths = public_source_paths(root)
    for tracked in sorted(path for path in tracked_paths if is_generated_public_path(path)):
        findings.append({"category": "generated_path_tracked", "path": tracked})
    for relative in source_paths:
        item = root / relative
        if is_generated_public_path(relative):
            continue
        if item.is_symlink():
            findings.append({"category": "symlink", "path": relative})
            continue
        if not item.is_file():
            continue
        lowered = relative.lower()
        if any(
            token in lowered
            for token in (
                "research-events.jsonl",
                "project-record.json",
                "run-trace.json",
                "browser-capture",
                ".pem",
                ".p12",
                ".pfx",
            )
        ):
            findings.append({"category": "private_evidence_name", "path": relative})
        if item.stat().st_size > 16_000_000:
            findings.append({"category": "oversized_public_file", "path": relative})
            continue
        data = item.read_bytes()
        if b"\x00" in data:
            findings.append({"category": "binary_payload", "path": relative})
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            findings.append({"category": "non_utf8_payload", "path": relative})
            continue
        findings.extend(_text_findings(relative, text))
    if gitleaks_binary is not None:
        with tempfile.TemporaryDirectory() as directory:
            scan_root = Path(directory)
            for relative in source_paths:
                source = root / relative
                if (
                    is_generated_public_path(relative)
                    or source.is_symlink()
                    or not source.is_file()
                ):
                    continue
                target = scan_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            findings.extend(run_gitleaks(gitleaks_binary, scan_root))
    elif require_gitleaks:
        findings.append({"category": "gitleaks_unavailable", "path": "."})
    deduplicated = [dict(row) for row in {tuple(sorted(item.items())) for item in findings}]
    deduplicated.sort(key=lambda row: (row["category"], row["path"]))
    return {
        "contract": "d18.clean_public_tree_verification/v1",
        "status": "passed" if not deduplicated else "failed",
        "public_tree_sha256": actual_tree,
        "finding_count": len(deduplicated),
        "findings": deduplicated,
        "gitleaks_verified": gitleaks_binary is not None,
    }


def verify_clean_history(repository: Path) -> dict[str, object]:
    root = Path(repository).resolve()
    count = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--count", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    identities = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%an%x00%ae%x00%cn%x00%ce"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout
    roots = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--max-parents=0", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.splitlines()
    merge_commits = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--min-parents=2", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.splitlines()
    allowed = {
        "D-18 Release Bot",
        "release-bot@users.noreply.github.com",
        "release-bot@example.invalid",
    }
    values = {
        value.strip()
        for line in identities.splitlines()
        for value in line.split("\x00")
        if value.strip()
    }
    errors = []
    if len(roots) != 1:
        errors.append("public history does not have exactly one clean root")
    if merge_commits:
        errors.append("public history is not linear")
    if not values or not values.issubset(allowed):
        errors.append("public history exposes an unapproved identity")
    return {
        "contract": "d18.clean_public_history_verification/v2",
        "status": "passed" if not errors else "failed",
        "commit_count": int(count),
        "root_count": len(roots),
        "merge_commit_count": len(merge_commits),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--gitleaks", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = verify_clean_public_tree(
        args.staging,
        manifest,
        gitleaks_binary=args.gitleaks,
        require_gitleaks=True,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
