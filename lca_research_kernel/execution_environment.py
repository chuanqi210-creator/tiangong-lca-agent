"""Project-scoped least-authority execution identity and authorization receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Sequence

EXECUTION_POLICY_CONTRACT = "lca_research_kernel.project_execution_policy/v1"
EXECUTION_IDENTITY_CONTRACT = "lca_research_kernel.project_execution_identity/v1"
OPERATION_RECEIPT_CONTRACT = (
    "lca_research_kernel.project_operation_authorization/v1"
)
RESEARCH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
FLOATING = {"latest", "head", "main", "master", "*", "develop", "trunk"}
SHELL_MARKERS = ("$(", "`", "&&", "||", ";", "\n", "\r")
INJECTION_MARKERS = (
    "ignore previous",
    "ignore the goal",
    "override system",
    "grant external_write",
    "change permissions",
)
SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "private_key",
}
SENSITIVE_PATTERNS = (
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{4,}", re.IGNORECASE),
    re.compile(r"\b(?:api[_ -]?key|password|secret)\s*[=:]", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _sensitive(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in {
                re.sub(r"[^a-z0-9]", "", item.lower())
                for item in SENSITIVE_KEYS
            }:
                return True
            if _sensitive(child):
                return True
    elif isinstance(value, list):
        return any(_sensitive(item) for item in value)
    elif isinstance(value, str):
        return any(pattern.search(value) for pattern in SENSITIVE_PATTERNS)
    return False


def _validate_policy(value: Mapping[str, object]) -> dict:
    required = {
        "contract",
        "policy_id",
        "file_rules",
        "network_rules",
        "subprocess_rules",
        "service_rules",
        "effect_rules",
        "output_rules",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != required
        or value.get("contract") != EXECUTION_POLICY_CONTRACT
    ):
        raise ValueError("project execution policy contract is invalid")
    policy = deepcopy(dict(value))
    if not isinstance(policy["policy_id"], str) or not policy["policy_id"]:
        raise ValueError("project execution policy identity is absent")
    file_rules = policy["file_rules"]
    network = policy["network_rules"]
    subprocess_rules = policy["subprocess_rules"]
    effects = policy["effect_rules"]
    output = policy["output_rules"]
    if (
        not isinstance(file_rules, Mapping)
        or set(file_rules) != {"read_roots", "write_roots"}
        or not isinstance(network, Mapping)
        or set(network) != {"external_read_hosts", "external_write_hosts"}
        or not isinstance(subprocess_rules, Mapping)
        or set(subprocess_rules) != {
            "allowed_executables",
            "environment_allowlist",
        }
        or not isinstance(effects, Mapping)
        or set(effects) != {"autonomous", "human_owned"}
        or not isinstance(output, Mapping)
        or not isinstance(output.get("max_bytes"), int)
        or output["max_bytes"] < 1
        or output.get("secrets_forbidden") is not True
    ):
        raise ValueError("project execution policy sections are invalid")
    for rows in (
        file_rules["read_roots"],
        file_rules["write_roots"],
        network["external_read_hosts"],
        network["external_write_hosts"],
        subprocess_rules["allowed_executables"],
        subprocess_rules["environment_allowlist"],
        effects["autonomous"],
        effects["human_owned"],
    ):
        if not isinstance(rows, list) or any(
            not isinstance(item, str) or not item for item in rows
        ):
            raise ValueError("project execution policy allowlist is invalid")
    if set(effects["autonomous"]) & set(effects["human_owned"]):
        raise ValueError("project execution effect ownership overlaps")
    if policy["network_rules"]["external_write_hosts"]:
        raise ValueError("project execution policy cannot pre-authorize external writes")
    if not isinstance(policy["service_rules"], Mapping):
        raise ValueError("project execution service policy is invalid")
    return policy


class ProjectExecutionEnvironment:
    """One project identity with content-bound least-authority receipts."""

    def __init__(self, root: Path, research_id: str, policy: Mapping[str, object]):
        self.root = Path(root).resolve()
        self.research_id = research_id
        self.policy = _validate_policy(policy)
        self.policy_sha256 = _sha256(self.policy)
        self.project_root = self.root / "projects" / research_id / "execution"
        self.receipt_root = self.project_root / "authorization-receipts"
        self.identity = {
            "contract": EXECUTION_IDENTITY_CONTRACT,
            "research_id": research_id,
            "project_root": str(self.project_root),
            "policy_id": self.policy["policy_id"],
            "policy_sha256": self.policy_sha256,
            "execution_id": "execution-" + _sha256(
                {
                    "research_id": research_id,
                    "project_root": str(self.project_root),
                    "policy_sha256": self.policy_sha256,
                }
            )[:24],
        }

    @classmethod
    def create(
        cls,
        root: Path,
        research_id: str,
        policy: Mapping[str, object],
    ) -> "ProjectExecutionEnvironment":
        if (
            not isinstance(research_id, str)
            or RESEARCH_ID_RE.fullmatch(research_id) is None
        ):
            raise ValueError("project execution research_id is invalid")
        environment = cls(root, research_id, policy)
        for relative in set(
            environment.policy["file_rules"]["read_roots"]
            + environment.policy["file_rules"]["write_roots"]
        ):
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("project execution file root is unsafe")
            (environment.project_root / path).mkdir(parents=True, exist_ok=True)
        identity_path = environment.project_root / "execution-identity.json"
        content = _canonical_bytes(environment.identity) + b"\n"
        if identity_path.is_file() and identity_path.read_bytes() != content:
            raise ValueError("project execution identity drifted")
        _atomic_write(identity_path, content)
        return environment

    def authorize_file(self, path: Path, operation: str) -> dict:
        if operation not in {"read", "write"}:
            raise ValueError("project file operation is unsupported")
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        resolved = candidate.resolve(strict=False)
        roots = self.policy["file_rules"][
            "read_roots" if operation == "read" else "write_roots"
        ]
        allowed = []
        for relative in roots:
            root = (self.project_root / relative).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            allowed.append(root)
        if not allowed:
            raise ValueError("project file path is outside allowed roots or escapes")
        return self._simple_receipt(
            "file",
            {
                "operation": operation,
                "path": str(resolved),
                "allowed_root": str(allowed[0]),
            },
        )

    def authorize_network(self, host: str, operation: str) -> dict:
        if not isinstance(host, str) or not host or "/" in host or ":" in host:
            raise ValueError("project network host is invalid")
        if operation not in {"external_read", "external_write"}:
            raise ValueError("project network operation is unsupported")
        allowed = self.policy["network_rules"][
            "external_read_hosts"
            if operation == "external_read"
            else "external_write_hosts"
        ]
        if host.lower() not in {str(item).lower() for item in allowed}:
            raise PermissionError("project network host or operation is not authorized")
        return self._simple_receipt(
            "network", {"host": host.lower(), "operation": operation}
        )

    def authorize_subprocess(self, argv: Sequence[str]) -> dict:
        if (
            not isinstance(argv, Sequence)
            or isinstance(argv, (str, bytes))
            or not argv
            or any(not isinstance(item, str) or not item for item in argv)
        ):
            raise ValueError("project subprocess argv is invalid")
        executable = Path(argv[0])
        if not executable.is_absolute():
            raise ValueError("project subprocess executable must be an absolute path")
        allowed = set()
        for item in self.policy["subprocess_rules"]["allowed_executables"]:
            candidate = Path(item)
            if not candidate.is_absolute():
                candidate = self.root / candidate
            allowed.add(str(candidate.resolve(strict=False)))
        resolved = str(executable.resolve(strict=False))
        if resolved not in allowed:
            raise PermissionError("project subprocess executable is not authorized")
        if any(any(marker in arg for marker in SHELL_MARKERS) for arg in argv[1:]):
            raise ValueError("project subprocess shell syntax is forbidden")
        return self._simple_receipt(
            "subprocess",
            {
                "argv_sha256": _sha256(list(argv)),
                "executable": resolved,
                "shell": False,
            },
        )

    def authorize_tool(
        self,
        capability: Mapping[str, object],
        arguments: Mapping[str, object],
        *,
        operation_id: str,
    ) -> dict:
        if (
            not isinstance(operation_id, str)
            or OPERATION_ID_RE.fullmatch(operation_id) is None
        ):
            raise ValueError("project tool operation_id is invalid")
        if not isinstance(capability, Mapping) or not isinstance(arguments, Mapping):
            raise ValueError("project tool authorization input is invalid")
        capability_id = capability.get("capability_id")
        effect = capability.get("effect")
        entrypoint = capability.get("entrypoint")
        if not isinstance(capability_id, str) or not capability_id:
            raise ValueError("project tool capability identity is absent")
        if effect not in self.policy["effect_rules"]["autonomous"]:
            raise PermissionError("project tool effect is human-owned or unsupported")
        source = capability.get("source_lock")
        if not isinstance(source, Mapping):
            raise ValueError("project tool source lock is absent")
        version = source.get("version")
        integrity = source.get("integrity")
        if (
            not isinstance(source.get("identity"), str)
            or not isinstance(version, str)
            or not version
            or version.lower() in FLOATING
            or not isinstance(integrity, str)
            or not integrity
        ):
            raise ValueError("project tool source identity is unpinned")
        contract = capability.get("tool_contract")
        if not isinstance(contract, Mapping):
            raise ValueError("project tool contract is absent")
        if (
            contract.get("name") != entrypoint
            or not isinstance(contract.get("description"), str)
            or not isinstance(contract.get("input_schema"), Mapping)
        ):
            raise ValueError("project tool name or contract is invalid")
        metadata_text = json.dumps(
            {
                "description": contract["description"],
                "capability_id": capability_id,
            },
            ensure_ascii=False,
        ).lower()
        if any(marker in metadata_text for marker in INJECTION_MARKERS):
            raise ValueError("project tool metadata contains a control injection")
        self._validate_arguments(contract["input_schema"], arguments)
        basis = {
            "operation_id": operation_id,
            "execution_id": self.identity["execution_id"],
            "policy_sha256": self.policy_sha256,
            "capability_id": capability_id,
            "effect": effect,
            "source_lock_sha256": _sha256(source),
            "arguments_sha256": _sha256(arguments),
            "entrypoint": deepcopy(entrypoint),
        }
        receipt = {
            "contract": OPERATION_RECEIPT_CONTRACT,
            "status": "authorized",
            **basis,
        }
        receipt["receipt_sha256"] = _sha256(receipt)
        path = self.receipt_root / f"{operation_id}.json"
        content = _canonical_bytes(receipt) + b"\n"
        if path.is_file():
            if path.read_bytes() != content:
                raise ValueError(
                    "project tool operation_id was reused with different content"
                )
            return deepcopy(receipt)
        _atomic_write(path, content)
        return deepcopy(receipt)

    def validate_operation_receipt(
        self,
        receipt: Mapping[str, object],
        identity: Mapping[str, object],
        *,
        capability_id: str,
        effect: str,
        arguments: Mapping[str, object],
    ) -> dict:
        if identity != self.identity:
            raise ValueError("project execution identity drifted")
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("contract") != OPERATION_RECEIPT_CONTRACT
        ):
            raise ValueError("project execution operation receipt is absent")
        normalized = deepcopy(dict(receipt))
        digest = normalized.pop("receipt_sha256", None)
        if digest != _sha256(normalized):
            raise ValueError("project execution operation receipt drifted")
        if (
            normalized.get("execution_id") != self.identity["execution_id"]
            or normalized.get("policy_sha256") != self.policy_sha256
            or normalized.get("capability_id") != capability_id
            or normalized.get("effect") != effect
            or normalized.get("arguments_sha256") != _sha256(arguments)
        ):
            raise ValueError("project execution operation receipt does not match request")
        operation_id = normalized.get("operation_id")
        if not isinstance(operation_id, str):
            raise ValueError("project execution operation receipt identity is absent")
        path = self.receipt_root / f"{operation_id}.json"
        if not path.is_file() or path.read_bytes() != _canonical_bytes(receipt) + b"\n":
            raise ValueError("project execution operation receipt is unregistered")
        return deepcopy(dict(receipt))

    def validate_result(self, value: object) -> dict:
        if _sensitive(value):
            raise ValueError("project runtime output contains sensitive material")
        size = len(_canonical_bytes(value))
        if size > self.policy["output_rules"]["max_bytes"]:
            raise ValueError("project runtime output exceeds the size boundary")
        return {
            "contract": "lca_research_kernel.runtime_output_acceptance/v1",
            "status": "accepted_runtime_output",
            "output_sha256": _sha256(value),
            "bytes": size,
            "policy_sha256": self.policy_sha256,
        }

    def _validate_arguments(
        self, schema: Mapping[str, object], arguments: Mapping[str, object]
    ) -> None:
        if schema.get("type") != "object":
            raise ValueError("project tool input schema is unsupported")
        required = schema.get("required") or []
        properties = schema.get("properties") or {}
        if not isinstance(required, list) or not isinstance(properties, Mapping):
            raise ValueError("project tool input schema is invalid")
        if any(key not in arguments for key in required):
            raise ValueError("project tool arguments do not satisfy schema")
        if schema.get("additionalProperties") is False and set(arguments) - set(
            properties
        ):
            raise ValueError("project tool arguments do not satisfy schema")
        for key, value in arguments.items():
            descriptor = properties.get(key)
            if not isinstance(descriptor, Mapping):
                continue
            expected = descriptor.get("type")
            types = {
                "string": str,
                "integer": int,
                "number": (int, float),
                "boolean": bool,
                "object": Mapping,
                "array": list,
            }
            if expected in types and not isinstance(value, types[expected]):
                raise ValueError("project tool arguments do not satisfy schema")
        if _sensitive(arguments):
            raise ValueError("project tool arguments contain sensitive material")

    def _simple_receipt(self, kind: str, payload: Mapping[str, object]) -> dict:
        receipt = {
            "contract": "lca_research_kernel.execution_authorization/v1",
            "status": "authorized",
            "kind": kind,
            "execution_id": self.identity["execution_id"],
            "policy_sha256": self.policy_sha256,
            "payload": deepcopy(dict(payload)),
        }
        receipt["receipt_sha256"] = _sha256(receipt)
        return receipt
