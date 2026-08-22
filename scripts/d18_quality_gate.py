#!/usr/bin/env python3
"""Repository-owned D-18 continuous quality policy and execution boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = Path(".github/d18-quality-policy.json")
_SHA40 = re.compile(r"[0-9a-f]{40}")
_SHA64 = re.compile(r"[0-9a-f]{64}")
_FAMILIES = (
    "contracts",
    "backend_kernel",
    "frontend_product",
    "agent_behavior",
    "security_boundary",
    "container_runtime",
)
_REQUIRED_JOBS = (
    "contract-policy",
    "backend-kernel",
    "frontend-product",
    "agent-behavior",
    "security-boundary",
    "container-runtime",
)
_ACTION_PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/setup-node": "820762786026740c76f36085b0efc47a31fe5020",
}
_SAFE_ENVIRONMENT = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "SYSTEMROOT",
    "WINDIR",
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_policy_sha256(policy: Mapping[str, object]) -> str:
    unsigned = dict(policy)
    unsigned.pop("policy_sha256", None)
    return hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()


def _safe_relative_path(raw: object, *, label: str) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise ValueError(f"{label} must be a non-empty POSIX path")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} is unsafe")
    return raw


def _exact_string_list(value: object, *, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"{label} must be a unique non-empty string list")
    return value


def validate_quality_policy(policy: object, *, root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    if not isinstance(policy, dict):
        raise ValueError("quality policy must contain an object")
    required_keys = {
        "contract",
        "policy_sha256",
        "fail_closed_unknown",
        "families",
        "required_jobs",
        "aggregate",
        "scheduled",
        "runtimes",
        "actions",
        "suites",
        "path_rules",
        "local_commands",
        "workflow_paths",
    }
    if set(policy) != required_keys:
        raise ValueError("quality policy fields are invalid")
    if policy.get("contract") != "d18_continuous_quality_policy/v1":
        raise ValueError("quality policy contract is invalid")
    digest = policy.get("policy_sha256")
    if not isinstance(digest, str) or not _SHA64.fullmatch(digest):
        raise ValueError("quality policy digest is invalid")
    if digest != canonical_policy_sha256(policy):
        raise ValueError("quality policy digest does not match content")
    if policy.get("fail_closed_unknown") is not True:
        raise ValueError("quality policy must fail closed for unknown paths")
    if tuple(policy.get("families", ())) != _FAMILIES:
        raise ValueError("quality policy families are invalid")
    if tuple(policy.get("required_jobs", ())) != _REQUIRED_JOBS:
        raise ValueError("quality policy required jobs are invalid")
    if policy.get("aggregate") != {
        "job_id": "d18-quality-gate",
        "check_name": "D-18 Quality Gate",
    }:
        raise ValueError("quality policy aggregate gate is invalid")
    if policy.get("runtimes") != {
        "python": "3.11.15",
        "python_darwin": "3.11.9",
        "node": "24.18.0",
        "pnpm": "11.13.0",
    }:
        raise ValueError("quality policy runtimes are invalid")
    if policy.get("scheduled") != {
        "aggregate": {
            "check_name": "D-18 Scheduled Quality Gate",
            "job_id": "scheduled-quality-gate",
        },
        "cron": "23 19 * * 1",
        "required_jobs": [
            "linux-runtime",
            "darwin-runtime",
            "frontend-production",
        ],
        "runners": {
            "linux-runtime": "ubuntu-24.04",
            "darwin-runtime": "macos-15",
            "frontend-production": "ubuntu-24.04",
        },
    }:
        raise ValueError("scheduled quality policy is invalid")
    if policy.get("actions") != _ACTION_PINS or any(
        not _SHA40.fullmatch(value) for value in policy["actions"].values()
    ):
        raise ValueError("quality policy Action pins are invalid")

    suites = policy.get("suites")
    if not isinstance(suites, dict) or set(suites) != {
        "agent_behavior",
        "security_boundary",
        "container_runtime",
    }:
        raise ValueError("quality policy suite registry is invalid")
    for suite_id, suite in suites.items():
        if not isinstance(suite, dict) or set(suite) != {"family", "modules"}:
            raise ValueError(f"quality suite {suite_id} is invalid")
        if suite["family"] not in _FAMILIES:
            raise ValueError(f"quality suite {suite_id} family is invalid")
        modules = _exact_string_list(
            suite["modules"], label=f"quality suite {suite_id} modules"
        )
        for module in modules:
            if re.fullmatch(r"tests\.test_[a-z0-9_]+", module) is None:
                raise ValueError(f"quality suite {suite_id} module is invalid")
            module_path = root.joinpath(*module.split(".")).with_suffix(".py")
            if not module_path.is_file():
                raise ValueError(f"quality suite module is unavailable: {module}")

    path_rules = policy.get("path_rules")
    if not isinstance(path_rules, list) or not path_rules:
        raise ValueError("quality policy path rules are invalid")
    identities: set[tuple[str, str]] = set()
    for rule in path_rules:
        if not isinstance(rule, dict) or set(rule) != {"kind", "pattern", "families"}:
            raise ValueError("quality policy path rule is invalid")
        if rule["kind"] not in {"prefix", "exact"}:
            raise ValueError("quality policy path rule kind is invalid")
        pattern = _safe_relative_path(rule["pattern"], label="path rule pattern")
        identity = (rule["kind"], pattern)
        if identity in identities:
            raise ValueError("quality policy path rule is duplicated")
        identities.add(identity)
        families = _exact_string_list(rule["families"], label="path rule families")
        if any(family not in _FAMILIES for family in families):
            raise ValueError("quality policy path rule family is invalid")

    commands = policy.get("local_commands")
    expected_commands = {
        "contract-policy": "make verify-quality-policy",
        "backend-kernel": "make verify",
        "frontend-product": "make verify-frontend",
        "agent-behavior": "make verify-agent-quality",
        "security-boundary": "make verify-security-boundary",
        "container-runtime": "python /app/scripts/check_d18_container_runtime.py",
    }
    if commands != expected_commands:
        raise ValueError("quality policy local commands are invalid")
    workflows = policy.get("workflow_paths")
    if not isinstance(workflows, dict) or set(workflows) != {"pr", "scheduled"}:
        raise ValueError("quality policy workflow paths are invalid")
    for key, path in workflows.items():
        _safe_relative_path(path, label=f"workflow path {key}")
        if not str(path).startswith(".github/workflows/") or not str(path).endswith(
            (".yml", ".yaml")
        ):
            raise ValueError("quality policy workflow path is invalid")
    return policy


def load_quality_policy(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    path = root / POLICY_PATH
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("quality policy is unavailable or invalid") from error
    return validate_quality_policy(policy, root=root)


def _validate_changed_paths(changed_paths: Sequence[str]) -> tuple[str, ...]:
    if (
        not isinstance(changed_paths, Sequence)
        or isinstance(changed_paths, (str, bytes))
        or not changed_paths
    ):
        raise ValueError("changed paths must be a non-empty sequence")
    normalized = tuple(
        _safe_relative_path(path, label="changed path") for path in changed_paths
    )
    if len(normalized) != len(set(normalized)):
        raise ValueError("changed paths must be unique")
    return normalized


def classify_changes(
    policy: Mapping[str, object], changed_paths: Sequence[str]
) -> tuple[str, ...]:
    paths = _validate_changed_paths(changed_paths)
    families = tuple(policy["families"])
    selected: set[str] = set()
    for path in paths:
        matched = False
        for rule in policy["path_rules"]:
            matches = (
                path == rule["pattern"]
                if rule["kind"] == "exact"
                else path.startswith(rule["pattern"])
            )
            if matches:
                selected.update(rule["families"])
                matched = True
        if not matched:
            return families
    return tuple(family for family in families if family in selected)


def _sanitized_environment() -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in _SAFE_ENVIRONMENT
        if name in os.environ
    }
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def run_suite(
    policy: Mapping[str, object],
    suite_id: str,
    *,
    root: Path,
    python: str,
    runner: Callable[..., Any] = subprocess.run,
) -> int:
    suites = policy["suites"]
    if suite_id not in suites:
        raise ValueError("quality suite is not registered")
    command = [
        str(python),
        "-m",
        "unittest",
        *suites[suite_id]["modules"],
        "-v",
    ]
    completed = runner(
        command,
        cwd=Path(root).resolve(),
        check=False,
        env=_sanitized_environment(),
    )
    return int(completed.returncode)


def verify_workflow(
    policy: Mapping[str, object],
    workflow_path: Path,
    *,
    scheduled: bool = False,
) -> dict[str, object]:
    """Verify security-sensitive workflow invariants without executing YAML."""

    try:
        text = Path(workflow_path).read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError("quality workflow is unavailable") from error
    lowered = text.casefold()
    for line in text.splitlines():
        run_match = re.match(r"^\s+run:\s*(.*?)\s*$", line)
        if run_match is not None and run_match.group(1) != "|":
            raise ValueError("quality workflow run commands must use block scalars")
    if "pull_request_target" in text or "${{ secrets." in lowered:
        raise ValueError("quality workflow uses a prohibited trust boundary")
    if re.search(r"(?m)^permissions:\s*$", text) is None:
        raise ValueError("quality workflow permissions are absent")
    if re.search(r"(?m)^\s{2}contents:\s*read\s*$", text) is None:
        raise ValueError("quality workflow contents permission is invalid")
    if re.search(r"(?m)^\s+[a-z-]+:\s*write\s*$", lowered):
        raise ValueError("quality workflow has write permission")
    if "cancel-in-progress: true" not in lowered:
        raise ValueError("quality workflow concurrency cancellation is absent")
    for match in re.finditer(r"(?m)^\s*-?\s*uses:\s*([^\s#]+)", text):
        reference = match.group(1)
        if "@" not in reference:
            raise ValueError("quality workflow Action reference is invalid")
        action, revision = reference.rsplit("@", 1)
        if policy["actions"].get(action) != revision:
            raise ValueError("quality workflow Action is unregistered or floating")
    if scheduled:
        for trigger in ("schedule:", "workflow_dispatch:"):
            if trigger not in text:
                raise ValueError("scheduled quality workflow trigger is absent")
        scheduled_policy = policy["scheduled"]
        if f"- cron: \"{scheduled_policy['cron']}\"" not in text:
            raise ValueError("scheduled quality workflow cron drifted")
        for job in scheduled_policy["required_jobs"]:
            match = re.search(
                rf"(?ms)^  {re.escape(job)}:\s*\n(?P<body>.*?)(?=^  [a-z0-9-]+:\s*$|\Z)",
                text,
            )
            if match is None:
                raise ValueError("scheduled quality workflow job is absent")
            expected_runner = scheduled_policy["runners"][job]
            if re.search(
                rf"(?m)^    runs-on:\s*{re.escape(expected_runner)}\s*$",
                match.group("body"),
            ) is None:
                raise ValueError("scheduled quality workflow runner drifted")
        aggregate = scheduled_policy["aggregate"]
        aggregate_match = re.search(
            rf"(?ms)^  {re.escape(aggregate['job_id'])}:\s*\n(?P<body>.*?)(?=^  [a-z0-9-]+:\s*$|\Z)",
            text,
        )
        if aggregate_match is None:
            raise ValueError("scheduled aggregate quality job is absent")
        aggregate_body = aggregate_match.group("body")
        if f"name: {aggregate['check_name']}" not in aggregate_body:
            raise ValueError("scheduled aggregate quality check name is invalid")
        if re.search(r"(?m)^    if:\s*always\(\)\s*$", aggregate_body) is None:
            raise ValueError("scheduled aggregate quality check is not fail-closed")
        for job in scheduled_policy["required_jobs"]:
            if re.search(rf"(?m)^      - {re.escape(job)}\s*$", aggregate_body) is None:
                raise ValueError("scheduled aggregate dependency is absent")
        for command in (
            policy["local_commands"]["security-boundary"],
            policy["local_commands"]["frontend-product"],
            policy["local_commands"]["container-runtime"],
        ):
            if command not in text:
                raise ValueError("scheduled quality workflow command drifted")
    else:
        for trigger in ("pull_request:", "push:", "merge_group:", "workflow_dispatch:"):
            if trigger not in text:
                raise ValueError("pull request quality workflow trigger is absent")
        for job in (*policy["required_jobs"], policy["aggregate"]["job_id"]):
            if re.search(rf"(?m)^  {re.escape(job)}:\s*$", text) is None:
                raise ValueError("pull request quality workflow job is absent")
        if f"name: {policy['aggregate']['check_name']}" not in text:
            raise ValueError("aggregate quality check name is invalid")
        aggregate_match = re.search(
            rf"(?ms)^  {re.escape(policy['aggregate']['job_id'])}:\s*\n(?P<body>.*?)(?=^  [a-z0-9-]+:\s*$|\Z)",
            text,
        )
        if aggregate_match is None:
            raise ValueError("aggregate quality job is absent")
        aggregate_body = aggregate_match.group("body")
        if re.search(r"(?m)^    if:\s*always\(\)\s*$", aggregate_body) is None:
            raise ValueError("aggregate quality check is not fail-closed")
        for job in policy["required_jobs"]:
            if re.search(rf"(?m)^      - {re.escape(job)}\s*$", aggregate_body) is None:
                raise ValueError("aggregate quality dependency is absent")
        for command in policy["local_commands"].values():
            if command not in text:
                raise ValueError("quality workflow command drifted")
    return {
        "contract": "d18_quality_workflow_verification/v1",
        "status": "passed",
        "path": Path(workflow_path).name,
        "scheduled": scheduled,
    }


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("verify-policy", "verify-workflows"):
        command = subparsers.add_parser(name)
        command.add_argument("--root", type=Path, default=ROOT)
        if name == "verify-workflows":
            command.add_argument("--pr-only", action="store_true")
    classify = subparsers.add_parser("classify")
    classify.add_argument("--root", type=Path, default=ROOT)
    classify.add_argument("--path", action="append", required=True)
    suite = subparsers.add_parser("run-suite")
    suite.add_argument("--root", type=Path, default=ROOT)
    suite.add_argument("suite_id")
    suite.add_argument("--python", default=sys.executable)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    root = arguments.root.resolve()
    policy = load_quality_policy(root)
    if arguments.command == "verify-policy":
        _print_json(
            {
                "contract": policy["contract"],
                "policy_sha256": policy["policy_sha256"],
                "status": "passed",
            }
        )
        return 0
    if arguments.command == "classify":
        _print_json({"families": list(classify_changes(policy, arguments.path))})
        return 0
    if arguments.command == "run-suite":
        return run_suite(
            policy,
            arguments.suite_id,
            root=root,
            python=arguments.python,
        )
    reports = [
        verify_workflow(policy, root / policy["workflow_paths"]["pr"])
    ]
    if not arguments.pr_only:
        reports.append(
            verify_workflow(
                policy,
                root / policy["workflow_paths"]["scheduled"],
                scheduled=True,
            )
        )
    _print_json({"status": "passed", "workflows": reports})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
