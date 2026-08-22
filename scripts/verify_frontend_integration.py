#!/usr/bin/env python3
"""Verify immutable teammate-frontend provenance and local adoption records."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping


SOURCE_LOCK = Path("docs/frontend-integration/source-lock.json")
ADOPTION = Path("docs/frontend-integration/frontend-adoption.json")
ACCEPTANCE = Path("outputs/frontend-integration/acceptance.json")
MATERIAL_FIXTURES = Path(
    "outputs/d18-service/integration/material-fixtures/manifest.json"
)
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_INTEGRATION_PATHS = (
    Path("apps/web/scripts/check-d18-contract.mjs"),
    Path("apps/web/src/lib/d18/materials.ts"),
    Path("apps/web/src/lib/d18/proxy.ts"),
    MATERIAL_FIXTURES,
)
CORRECTION_CLASSES = frozenset(
    {
        "authority_boundary",
        "service_contract",
        "recovery",
        "presentation",
        "security",
        "developer_harness",
    }
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or "git command failed")
    return completed.stdout


def _raw_manifest(
    root: Path, commit: str, import_path: str
) -> tuple[str, list[str]]:
    output = _git(root, "ls-tree", "-r", "--full-tree", commit, "--", import_path)
    prefix = import_path.rstrip("/") + "/"
    rows: list[str] = []
    paths: list[str] = []
    for line in output.splitlines():
        metadata, path = line.split("\t", 1)
        blob = metadata.split()[2]
        if not path.startswith(prefix):
            raise ValueError("raw import contains a path outside its prefix")
        relative = path[len(prefix) :]
        rows.append(f"{relative}\t{blob}\n")
        paths.append(relative)
    payload = "".join(sorted(rows)).encode()
    return hashlib.sha256(payload).hexdigest(), sorted(paths)


def verify_frontend_source_lock(
    root: Path, *, source_lock: Mapping[str, Any] | None = None
) -> list[str]:
    root = Path(root).resolve()
    try:
        value = dict(source_lock) if source_lock is not None else _load(root / SOURCE_LOCK)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [f"frontend source lock is unavailable: {error}"]
    errors: list[str] = []
    expected = {
        "contract": "lca_research_agent.frontend_source_lock/v1",
        "repository": "https://github.com/JJchess/lca-agent-frontend",
        "upstream_commit": "1e31858790f00b539f56c45860fd6cf8cada4def",
        "upstream_tree": "4cf09abc196a200720695589a7580af3536092af",
        "import_path": "apps/web",
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            errors.append(f"frontend source lock {key} drifted")
    raw_commit = value.get("raw_import_commit")
    if not isinstance(raw_commit, str) or SHA1_RE.fullmatch(raw_commit) is None:
        errors.append("frontend raw import commit is invalid")
        return errors
    try:
        resolved = _git(root, "rev-parse", f"{raw_commit}^{{commit}}").strip()
        if resolved != raw_commit:
            errors.append("frontend raw import commit does not resolve exactly")
        digest, paths = _raw_manifest(root, raw_commit, str(value.get("import_path", "")))
    except ValueError as error:
        errors.append(f"frontend raw import is unavailable: {error}")
        return errors
    if value.get("file_manifest_sha256") != digest:
        errors.append("frontend raw import manifest drifted")
    if not paths:
        errors.append("frontend raw import is empty")
    prohibited = value.get("prohibited_paths")
    if not isinstance(prohibited, list) or any(
        not isinstance(item, str) or not item for item in prohibited
    ):
        errors.append("frontend prohibited path policy is invalid")
        prohibited = []
    for path in paths:
        parts = Path(path).parts
        if any(item in parts for item in prohibited):
            errors.append(f"frontend raw import contains prohibited path: {path}")
        lower = path.casefold()
        if any(
            marker in lower
            for marker in ("credentials.env", "api-key", "api_key", "secret-key")
        ):
            errors.append(f"frontend raw import contains secret-shaped path: {path}")
    if not isinstance(value.get("file_manifest_sha256"), str) or SHA256_RE.fullmatch(
        str(value.get("file_manifest_sha256"))
    ) is None:
        errors.append("frontend raw import manifest identity is invalid")
    return sorted(set(errors))


def verify_frontend_integration_paths(root: Path) -> list[str]:
    root = Path(root).resolve()
    return [
        f"frontend integration path is absent: {path.as_posix()}"
        for path in REQUIRED_INTEGRATION_PATHS
        if not (root / path).is_file()
    ]


def verify_material_fixtures(root: Path) -> list[str]:
    try:
        manifest = _load(Path(root).resolve() / MATERIAL_FIXTURES)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [f"material fixture manifest is unavailable: {error}"]
    errors: list[str] = []
    if manifest.get("contract") != (
        "lca_research_service.material_fixture_manifest/v1"
    ):
        errors.append("material fixture contract drifted")
    if manifest.get("candidate_only") is not True:
        errors.append("material fixtures are not candidate-only")
    expected_effects = {
        "external_writes": 0,
        "model_calls": 0,
        "network_calls": 0,
        "scientific_results": 0,
        "tool_calls": 0,
    }
    if manifest.get("effects") != expected_effects:
        errors.append("material fixture effects are not exactly zero")
    expected_scenarios = {
        "citation_jump",
        "delete_restore",
        "image_partial",
        "lca_package",
        "ordinary_document",
        "pause_resume",
        "processing_failure",
        "upload",
        "url_normalization",
        "waiting_for_user",
        "zip",
    }
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, Mapping) or set(scenarios) != expected_scenarios:
        errors.append("material fixture scenarios are incomplete or drifted")
    else:
        for name, value in scenarios.items():
            if (
                not isinstance(value, Mapping)
                or set(value) != {"kind", "input", "events", "expected"}
                or not isinstance(value.get("kind"), str)
                or not value.get("kind")
                or not isinstance(value.get("input"), Mapping)
                or not value.get("input")
                or not isinstance(value.get("events"), list)
                or not value.get("events")
                or any(
                    not isinstance(event, Mapping)
                    or not isinstance(event.get("event"), str)
                    or not event.get("event")
                    or not isinstance(event.get("sequence"), int)
                    or isinstance(event.get("sequence"), bool)
                    or event.get("sequence") <= 0
                    for event in value.get("events", [])
                )
                or not isinstance(value.get("expected"), Mapping)
                or not value.get("expected")
            ):
                errors.append(f"material fixture scenario is invalid: {name}")
                continue
            sequences = [event["sequence"] for event in value["events"]]
            if sequences != sorted(set(sequences)):
                errors.append(f"material fixture event order drifted: {name}")
    return sorted(errors)


def _verify_ref(
    root: Path,
    reference: Any,
    *,
    label: str,
    historical_commit: str | None = None,
) -> list[str]:
    if not isinstance(reference, Mapping):
        return [f"{label} reference is invalid"]
    path = reference.get("path")
    digest = reference.get("sha256")
    if (
        not isinstance(path, str)
        or not path
        or Path(path).is_absolute()
        or ".." in Path(path).parts
        or not isinstance(digest, str)
        or SHA256_RE.fullmatch(digest) is None
    ):
        return [f"{label} reference identity is invalid"]
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return [f"{label} reference target is unavailable"]
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        if historical_commit is None:
            return [f"{label} reference drifted"]
        completed = subprocess.run(
            ["git", "show", f"{historical_commit}:{path}"],
            cwd=root,
            check=False,
            capture_output=True,
        )
        if (
            completed.returncode != 0
            or hashlib.sha256(completed.stdout).hexdigest() != digest
        ):
            return [f"{label} reference drifted"]
    return []


def verify_frontend_adoption(root: Path) -> list[str]:
    root = Path(root).resolve()
    errors: list[str] = []
    try:
        adoption = _load(root / ADOPTION)
        acceptance = _load(root / ACCEPTANCE)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [f"frontend adoption records are unavailable: {error}"]
    for required_tracked in (
        ACCEPTANCE,
        Path("outputs/frontend-integration/live-probe.json"),
    ):
        try:
            _git(root, "ls-files", "--error-unmatch", required_tracked.as_posix())
        except ValueError:
            errors.append(
                f"frontend acceptance evidence is not Git-tracked: {required_tracked}"
            )
    if adoption.get("contract") != "lca_research_agent.frontend_adoption/v1":
        errors.append("frontend adoption contract is unsupported")
    if acceptance.get("contract") != (
        "lca_research_agent.frontend_integration_acceptance/v1"
    ):
        errors.append("frontend acceptance contract is unsupported")
    if acceptance.get("accepted_code_basis") != adoption.get("accepted_code_basis"):
        errors.append("frontend acceptance code basis differs from adoption")
    raw_commit = adoption.get("raw_import_commit")
    accepted_basis = adoption.get("accepted_code_basis")
    if not all(
        isinstance(value, str) and SHA1_RE.fullmatch(value)
        for value in (raw_commit, accepted_basis)
    ):
        errors.append("frontend adoption commit identities are invalid")
    else:
        try:
            if _git(root, "merge-base", "--is-ancestor", raw_commit, accepted_basis):
                pass
            if _git(root, "merge-base", "--is-ancestor", accepted_basis, "HEAD"):
                pass
        except ValueError:
            errors.append("frontend adoption commits are not ancestors in order")
    errors.extend(
        _verify_ref(root, adoption.get("source_lock_ref"), label="source lock")
    )
    architecture_refs = adoption.get("architecture_refs")
    if not isinstance(architecture_refs, list) or not architecture_refs:
        errors.append("frontend architecture references are absent")
    else:
        for index, reference in enumerate(architecture_refs):
            errors.extend(
                _verify_ref(root, reference, label=f"frontend architecture {index}")
            )
    if acceptance.get("status") != "accepted":
        errors.append("frontend integration has not been accepted")
    if acceptance.get("next_route") != "run-first-frontend-assisted-agent-pilot":
        errors.append("frontend acceptance next route drifted")
    if acceptance.get("effects") != {
        "external_writes": 0,
        "model_calls": 0,
        "scientific_results": 0,
        "tool_calls": 0,
    }:
        errors.append("frontend acceptance forbidden effects are nonzero")
    final_verification = acceptance.get("final_verification")
    if not isinstance(final_verification, Mapping) or final_verification.get(
        "status"
    ) != "passed":
        errors.append("frontend final repository verification is not passed")
    refs = acceptance.get("artifact_refs")
    if not isinstance(refs, list) or not refs:
        errors.append("frontend acceptance artifact refs are absent")
    else:
        for index, reference in enumerate(refs):
            errors.extend(
                _verify_ref(
                    root,
                    reference,
                    label=f"frontend artifact {index}",
                    historical_commit=(
                        accepted_basis if isinstance(accepted_basis, str) else None
                    ),
                )
            )
        probe_refs = [
            reference
            for reference in refs
            if isinstance(reference, Mapping)
            and reference.get("path") == "outputs/frontend-integration/live-probe.json"
        ]
        if len(probe_refs) != 1:
            errors.append("frontend live-probe reference is not unique")
        else:
            try:
                probe = _load(root / str(probe_refs[0]["path"]))
            except (OSError, ValueError, json.JSONDecodeError) as error:
                errors.append(f"frontend live probe is unavailable: {error}")
            else:
                if probe.get("contract") != "lca_research_agent.frontend_live_probe/v1":
                    errors.append("frontend live-probe contract is unsupported")
                if probe.get("effects_observation_contract") != (
                    "lca_research_service.no_effect_observation/v1"
                ):
                    errors.append("frontend live-probe effects were not backend-observed")
                if probe.get("effects") != acceptance.get("effects"):
                    errors.append("frontend live-probe effects differ from acceptance")
                if not probe.get("through_frontend_bff") or not probe.get(
                    "sse_cursor_resumed"
                ):
                    errors.append("frontend live-probe route or recovery evidence failed")
    corrections = adoption.get("corrections")
    if not isinstance(corrections, list) or not corrections:
        errors.append("frontend adoption corrections are absent")
    else:
        identifiers = [row.get("id") for row in corrections if isinstance(row, Mapping)]
        if len(identifiers) != len(corrections) or len(set(identifiers)) != len(
            identifiers
        ):
            errors.append("frontend correction identities are invalid or duplicated")
        covered: set[str] = set()
        for row in corrections:
            if not isinstance(row, Mapping):
                errors.append("frontend correction is not an object")
                continue
            if row.get("classification") not in CORRECTION_CLASSES:
                errors.append(f"frontend correction class is invalid: {row.get('id')}")
            commit = row.get("implementation_commit")
            if not isinstance(commit, str) or SHA1_RE.fullmatch(commit) is None:
                errors.append(f"frontend correction commit is invalid: {row.get('id')}")
            else:
                try:
                    if _git(root, "rev-parse", f"{commit}^{{commit}}").strip() != commit:
                        errors.append(f"frontend correction commit drifted: {row.get('id')}")
                    _git(root, "merge-base", "--is-ancestor", commit, "HEAD")
                except ValueError:
                    errors.append(f"frontend correction commit is unavailable: {row.get('id')}")
            for field in ("d18_reason", "rollback", "refresh_trigger"):
                if not isinstance(row.get(field), str) or not row[field].strip():
                    errors.append(f"frontend correction {field} is absent: {row.get('id')}")
            current_paths = row.get("current_paths")
            if not isinstance(current_paths, list) or not current_paths:
                errors.append(f"frontend correction paths are absent: {row.get('id')}")
                continue
            for relative in current_paths:
                if not isinstance(relative, str) or not (root / relative).exists():
                    errors.append(f"frontend correction path is unavailable: {relative}")
                else:
                    covered.add(relative)
        if (
            isinstance(raw_commit, str)
            and isinstance(accepted_basis, str)
            and SHA1_RE.fullmatch(raw_commit)
            and SHA1_RE.fullmatch(accepted_basis)
        ):
            try:
                changed = set(
                    _git(
                        root,
                        "diff",
                        "--name-only",
                        f"{raw_commit}..{accepted_basis}",
                    ).splitlines()
                )
                for path in sorted(changed - covered):
                    errors.append(f"frontend adopted change is unclassified: {path}")
            except ValueError as error:
                errors.append(f"frontend correction coverage is unavailable: {error}")
    return sorted(set(errors))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = verify_frontend_source_lock(root)
    errors.extend(verify_frontend_integration_paths(root))
    errors.extend(verify_material_fixtures(root))
    errors.extend(verify_frontend_adoption(root))
    print(
        json.dumps(
            {"status": "passed" if not errors else "failed", "errors": errors},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
