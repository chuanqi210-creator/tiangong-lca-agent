"""Bounded adapters for cataloged TianGong capability surfaces."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Callable, Mapping

from .capability_gateway import CapabilityGatewayError
from .project_record import canonical_sha256
from .runtime_environment_health import (
    OpenLcaServer,
    compile_openlca_calculation_server_argv,
)


SECRET_KEYS = {
    "api_key",
    "access_token",
    "password",
    "secret",
    "secret_key",
    "private_key",
    "refresh_token",
}


def _reject_secrets(value: object, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in SECRET_KEYS:
                raise ValueError(f"secret field is forbidden at {path}.{key}")
            _reject_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secrets(child, f"{path}[{index}]")


def _source(descriptor: Mapping[str, object]) -> tuple[str, str]:
    lock = descriptor.get("source_lock")
    if not isinstance(lock, Mapping):
        raise ValueError("capability source lock is absent")
    identity = lock.get("identity")
    version = lock.get("version")
    if not isinstance(identity, str) or not isinstance(version, str):
        raise ValueError("capability source identity or version is absent")
    return identity, version


class StaticJsonAdapter:
    def __init__(self, root: Path):
        self.root = Path(root)

    def execute(
        self,
        descriptor: Mapping[str, object],
        payload: Mapping[str, object],
        *,
        execution_identity: Mapping[str, object] = None,
        operation_receipt: Mapping[str, object] = None,
    ) -> dict:
        entrypoint = descriptor.get("entrypoint")
        if not isinstance(entrypoint, str):
            raise ValueError("static JSON entrypoint is invalid")
        relative = Path(entrypoint)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("static JSON entrypoint is unsafe")
        path = self.root / relative
        try:
            path.resolve().relative_to(self.root.resolve())
        except ValueError as error:
            raise ValueError("static JSON entrypoint is unsafe") from error
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CapabilityGatewayError(
                "invalid_static_capability",
                "static capability is unavailable or invalid",
                retryable=False,
                next_route=str(descriptor.get("recovery_route") or "repair-static-adapter"),
            ) from error
        identity, version = _source(descriptor)
        return {
            "content": content,
            "source_identity": identity,
            "source_version": version,
        }


class BoundedSubprocessAdapter:
    def __init__(
        self,
        *,
        allowed_executables: set[str],
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        max_output_bytes: int = 64 * 1024,
    ):
        self.allowed_executables = set(allowed_executables)
        self.runner = runner
        self.max_output_bytes = max_output_bytes

    def execute(
        self,
        descriptor: Mapping[str, object],
        payload: Mapping[str, object],
        *,
        execution_identity: Mapping[str, object] = None,
        operation_receipt: Mapping[str, object] = None,
    ) -> dict:
        _reject_secrets(payload)
        entrypoint = descriptor.get("entrypoint")
        if (
            not isinstance(entrypoint, list)
            or not entrypoint
            or any(not isinstance(item, str) or not item for item in entrypoint)
        ):
            raise ValueError("subprocess entrypoint must be an argv list")
        if entrypoint[0] not in self.allowed_executables:
            raise ValueError("subprocess executable is not allowlisted")
        if set(payload) - {"args"}:
            raise ValueError("subprocess payload fields are unsupported")
        args = payload.get("args", [])
        if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
            raise ValueError("subprocess args must be strings")
        argv = [*entrypoint, *args]
        timeout = descriptor.get("timeout_seconds")
        if not isinstance(timeout, int) or timeout <= 0:
            raise ValueError("subprocess timeout is invalid")
        try:
            completed = self.runner(
                argv,
                shell=False,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise CapabilityGatewayError(
                "timeout",
                "bounded subprocess timed out",
                retryable=True,
                next_route=str(descriptor.get("recovery_route") or "retry-subprocess"),
            ) from error
        stdout = completed.stdout or b""
        stderr = completed.stderr or b""
        if isinstance(stdout, str):
            stdout = stdout.encode("utf-8")
        if isinstance(stderr, str):
            stderr = stderr.encode("utf-8")
        content = {
            "returncode": completed.returncode,
            "stdout": stdout[: self.max_output_bytes].decode(
                "utf-8", errors="replace"
            ),
            "stdout_truncated": len(stdout) > self.max_output_bytes,
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        }
        if completed.returncode != 0:
            raise CapabilityGatewayError(
                "subprocess_failure",
                f"bounded subprocess exited with code {completed.returncode}",
                retryable=False,
                next_route=str(descriptor.get("recovery_route") or "repair-subprocess"),
            )
        identity, version = _source(descriptor)
        return {
            "content": content,
            "source_identity": identity,
            "source_version": version,
        }


class SkillMetadataAdapter:
    def __init__(self, root: Path | None = None):
        self.root = Path(root).resolve() if root is not None else None

    def execute(
        self,
        descriptor: Mapping[str, object],
        payload: Mapping[str, object],
        *,
        execution_identity: Mapping[str, object] = None,
        operation_receipt: Mapping[str, object] = None,
    ) -> dict:
        if payload:
            raise ValueError("Skill metadata adapter accepts no execution payload")
        identity, version = _source(descriptor)
        loaded = {}
        if self.root is not None:
            entrypoint = descriptor.get("entrypoint")
            if not isinstance(entrypoint, str):
                raise ValueError("Skill entrypoint is invalid")
            relative = Path(entrypoint)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Skill entrypoint is unsafe")
            path = (self.root / relative).resolve()
            try:
                path.relative_to(self.root)
            except ValueError as error:
                raise ValueError("Skill entrypoint is unsafe") from error
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as error:
                raise CapabilityGatewayError(
                    "skill_runtime_unavailable",
                    "installed Skill method is unavailable",
                    retryable=False,
                    next_route=str(
                        descriptor.get("recovery_route")
                        or "repair-installed-skill-runtime"
                    ),
                ) from error
            lines = content.splitlines()
            if not lines or lines[0] != "---":
                raise ValueError("Skill method frontmatter is absent")
            try:
                closing = lines.index("---", 1)
            except ValueError as error:
                raise ValueError("Skill method frontmatter is unterminated") from error
            frontmatter = {}
            for line in lines[1:closing]:
                key, separator, value = line.partition(":")
                if separator and key.strip() in {"name", "description"}:
                    frontmatter[key.strip()] = value.strip()
            method_name = frontmatter.get("name")
            if not method_name:
                raise ValueError("Skill method name is absent")
            loaded = {
                "method_name": method_name,
                "description": frontmatter.get("description"),
                "content_sha256": hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
            }
        return {
            "content": {
                "capability_id": descriptor.get("capability_id"),
                "entrypoint": deepcopy(descriptor.get("entrypoint")),
                "allowed_use": deepcopy(descriptor.get("allowed_use") or []),
                "disposition": descriptor.get("disposition"),
                "execution": (
                    "loaded_not_executed"
                    if self.root is not None
                    else "not_performed_metadata_only"
                ),
                **loaded,
            },
            "source_identity": identity,
            "source_version": version,
        }


class MCPHostAdapter:
    def __init__(self, client: object):
        self.client = client

    def discover_tools(self) -> list[dict]:
        cursor = None
        tools: dict[str, dict] = {}
        pages = 0
        while True:
            pages += 1
            if pages > 100:
                raise ValueError("MCP tool pagination exceeded the bounded limit")
            response = self.client.list_tools(cursor=cursor)  # type: ignore[attr-defined]
            if not isinstance(response, Mapping):
                raise ValueError("MCP tool list response is invalid")
            rows = response.get("tools")
            if not isinstance(rows, list):
                raise ValueError("MCP tool list is absent")
            for row in rows:
                if not isinstance(row, Mapping) or not isinstance(row.get("name"), str):
                    raise ValueError("MCP tool metadata is invalid")
                name = str(row["name"])
                if name in tools:
                    raise ValueError(f"duplicate MCP tool registration: {name}")
                schema = deepcopy(row.get("inputSchema"))
                tools[name] = {
                    "name": name,
                    "input_schema": schema,
                    "schema_sha256": hashlib.sha256(
                        json.dumps(
                            schema,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                }
            next_cursor = response.get("nextCursor")
            if next_cursor is None:
                break
            if not isinstance(next_cursor, str) or not next_cursor:
                raise ValueError("MCP next cursor is invalid")
            cursor = next_cursor
        return [tools[key] for key in sorted(tools)]

    def execute(
        self,
        descriptor: Mapping[str, object],
        payload: Mapping[str, object],
        *,
        execution_identity: Mapping[str, object] = None,
        operation_receipt: Mapping[str, object] = None,
    ) -> dict:
        name = descriptor.get("entrypoint")
        if not isinstance(name, str):
            raise ValueError("MCP tool entrypoint is invalid")
        result = self.client.call_tool(name, deepcopy(dict(payload)))  # type: ignore[attr-defined]
        identity, version = _source(descriptor)
        return {
            "content": deepcopy(result),
            "source_identity": identity,
            "source_version": version,
        }


def _exact_source_documents(
    repository_root: Path,
    source_refs: object,
) -> dict[str, dict]:
    if not isinstance(source_refs, list) or not source_refs:
        raise ValueError("source-bound capability refs are absent")
    documents = {}
    for row in source_refs:
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("path"), str)
            or not isinstance(row.get("sha256"), str)
        ):
            raise ValueError("source-bound capability ref is invalid")
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("source-bound capability ref is unsafe")
        path = (repository_root / relative).resolve()
        try:
            path.relative_to(repository_root)
        except ValueError as error:
            raise ValueError("source-bound capability ref escaped") from error
        try:
            content = path.read_bytes()
        except OSError as error:
            raise CapabilityGatewayError(
                "source_unavailable",
                "source-bound capability input is unavailable",
                retryable=False,
                next_route="restore-exact-calculation-source",
            ) from error
        if hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise CapabilityGatewayError(
                "source_drift",
                "source-bound capability input drifted",
                retryable=False,
                next_route="restore-exact-calculation-source",
            )
        try:
            value = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError("source-bound capability document is invalid") from error
        if not isinstance(value, dict):
            raise ValueError("source-bound capability document must be an object")
        documents[relative.as_posix()] = value
    return documents


def _tidas_text(value: object) -> str:
    rows = value if isinstance(value, list) else [value]
    for row in rows:
        if isinstance(row, Mapping):
            text = row.get("#text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        if isinstance(row, str) and row.strip():
            return row.strip()
    return "unnamed source-bound entity"


class SourceBoundTIDASValidationCapability:
    """Validate exact source-bound ILCD/TIDAS documents with pinned tidas-tools."""

    def __init__(
        self,
        repository_root: Path,
        runtime_root: Path,
        *,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.repository_root = Path(repository_root).resolve()
        self.runtime_root = Path(runtime_root).resolve()
        self.runner = runner

    def describe(self) -> dict:
        executable = self.runtime_root / "python/bin/tidas-validate"
        return {
            "capability_id": "tidas.validate",
            "authority": "TIDAS",
            "access": "validate",
            "effect": "local_read",
            "result_status": "sourced_result_not_fact",
            "transport": "subprocess_argv",
            "entrypoint": str(executable),
            "timeout_seconds": 60,
            "source_lock": {
                "identity": "tidas-tools",
                "version": "0.0.43",
                "integrity": (
                    "sha256-c85b39adbacf2174374f1d0f668f5eedfc7725a93dbe7627cc009e1c9e8297eb"
                ),
            },
        }

    def execute(
        self,
        payload: Mapping[str, object],
        *,
        execution_identity: Mapping[str, object] = None,
        operation_receipt: Mapping[str, object] = None,
    ) -> dict:
        _reject_secrets(payload)
        scientific = payload.get("scientific_inputs")
        digest = payload.get("scientific_inputs_sha256")
        if (
            not isinstance(scientific, Mapping)
            or not isinstance(digest, str)
            or canonical_sha256(scientific) != digest
        ):
            raise ValueError("TIDAS validation scientific input hash drifted")
        documents = _exact_source_documents(
            self.repository_root, payload.get("source_refs")
        )
        dataset_paths = sorted(
            self.repository_root / relative
            for relative in documents
            if any(
                f"/{kind}/" in f"/{relative}"
                for kind in (
                    "processes",
                    "flows",
                    "flowproperties",
                    "unitgroups",
                    "lifecyclemodels",
                )
            )
        )
        if not dataset_paths:
            raise ValueError("TIDAS validation has no exact dataset input")
        # The accepted export binds all referenced datasets below one immutable root.
        export_roots = {
            next(
                parent
                for parent in path.parents
                if (parent / "manifest.json").is_file()
            )
            for path in dataset_paths
        }
        if len(export_roots) != 1:
            raise ValueError("TIDAS validation source export root is ambiguous")
        export_root = next(iter(export_roots))
        descriptor = self.describe()
        argv = [
            descriptor["entrypoint"],
            "--input-dir",
            str(export_root),
            "--report-format",
            "json",
            "--data-format",
            "ilcd",
            "--jobs",
            "1",
        ]
        try:
            completed = self.runner(
                argv,
                shell=False,
                capture_output=True,
                check=False,
                timeout=descriptor["timeout_seconds"],
                env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "")},
            )
        except subprocess.TimeoutExpired as error:
            raise CapabilityGatewayError(
                "timeout",
                "TIDAS validation timed out",
                retryable=True,
                next_route="retry-same-tidas-validation",
            ) from error
        if completed.returncode != 0:
            raise CapabilityGatewayError(
                "tidas_validation_failure",
                "TIDAS validation process failed",
                retryable=False,
                next_route="repair-source-bound-tidas-input",
            )
        try:
            report = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise CapabilityGatewayError(
                "invalid_capability_result",
                "TIDAS validation report is invalid",
                retryable=False,
                next_route="repair-tidas-validator-runtime",
            ) from error
        if not isinstance(report, Mapping) or report.get("ok") is not True:
            raise CapabilityGatewayError(
                "tidas_validation_failure",
                "TIDAS validation rejected the source-bound export",
                retryable=False,
                next_route="repair-source-bound-tidas-input",
            )
        return {
            "content": {
                "validation_status": "passed",
                "scientific_inputs_sha256": digest,
                "validated_source_ref_count": len(documents),
                "validated_dataset_ref_count": len(dataset_paths),
                "validator": {
                    "identity": "tidas-tools",
                    "version": "0.0.43",
                    "data_format": "ilcd",
                    "report_sha256": canonical_sha256(report),
                },
                "issue_summary": deepcopy(report.get("summary")),
                "external_writes": 0,
            },
            "source_identity": "tidas-tools",
            "source_version": "0.0.43",
        }


class IsolatedOpenLCAIPCCapability:
    """Execute one exact model in a disposable project-local openLCA database."""

    def __init__(
        self,
        repository_root: Path,
        runtime_root: Path,
        *,
        runtime_executor: Callable[[dict, Path], Mapping[str, object]] | None = None,
    ):
        self.repository_root = Path(repository_root).resolve()
        self.runtime_root = Path(runtime_root).resolve()
        self.runtime_executor = runtime_executor

    def describe(self) -> dict:
        bridge = self.repository_root / (
            "runtime/tiangong-node/run-openlca-calculation.mjs"
        )
        if not bridge.is_file():
            bridge_sha256 = "0" * 64
        else:
            bridge_sha256 = hashlib.sha256(bridge.read_bytes()).hexdigest()
        return {
            "capability_id": "openlca.calculate",
            "authority": "OpenLCA",
            "access": "calculate",
            "effect": "local_write",
            "result_status": "sourced_result_not_fact",
            "transport": "loopback_json_rpc",
            "entrypoint": "openlca.calculate",
            "timeout_seconds": 120,
            "source_version": "openLCA-2.6.2+olca-ipc-2.6.2",
            "source_lock": {
                "identity": "openLCA+olca-ipc",
                "version": "openLCA-2.6.2+olca-ipc-2.6.2",
                "integrity": "sha256:" + canonical_sha256(
                    {
                        "openlca": (
                            "sha256-3c6b91203cecb734f0c488ebd8e9cca660245f6e827d0add96805b7b32321e25"
                        ),
                        "node_lock": (
                            "sha256-58e6cb7fb2dbc7dd1790084dbb213040935241a4f92163c93d3f27a91979ee6d"
                        ),
                        "bridge_sha256": bridge_sha256,
                    }
                ),
            },
        }

    def execute(
        self,
        payload: Mapping[str, object],
        *,
        execution_identity: Mapping[str, object] = None,
        operation_receipt: Mapping[str, object] = None,
    ) -> dict:
        _reject_secrets(payload)
        if not isinstance(execution_identity, Mapping):
            raise ValueError("openLCA execution identity is absent")
        scientific = payload.get("scientific_inputs")
        digest = payload.get("scientific_inputs_sha256")
        if (
            not isinstance(scientific, Mapping)
            or not isinstance(digest, str)
            or canonical_sha256(scientific) != digest
        ):
            raise ValueError("openLCA scientific input hash drifted")
        documents = _exact_source_documents(
            self.repository_root, payload.get("source_refs")
        )
        execution_spec = payload.get("execution_spec")
        if not isinstance(execution_spec, Mapping):
            raise ValueError("openLCA execution spec is absent")
        model = self._compile_model(scientific, execution_spec, documents)
        project_root = Path(str(execution_identity.get("project_root"))).resolve()
        runtime_root = project_root / "tmp" / ("openlca-" + digest[:16])
        runtime_root.mkdir(parents=True, exist_ok=True)
        executor = self.runtime_executor or self._execute_runtime
        content = executor(model, runtime_root)
        if not isinstance(content, Mapping):
            raise ValueError("openLCA runtime result is invalid")
        normalized = deepcopy(dict(content))
        normalized.update(
            {
                "scientific_inputs_sha256": digest,
                "input_model_sha256": canonical_sha256(model),
                "executed_external_writes": 0,
            }
        )
        return {
            "content": normalized,
            "source_identity": "openLCA IPC",
            "source_version": "2.6.2",
        }

    def _compile_model(
        self,
        scientific: Mapping[str, object],
        execution_spec: Mapping[str, object],
        documents: Mapping[str, Mapping[str, object]],
    ) -> dict:
        native = execution_spec.get("native_model")
        method_spec = execution_spec.get("impact_method")
        if not isinstance(native, Mapping) or not isinstance(method_spec, Mapping):
            raise ValueError("openLCA execution spec sections are absent")
        if native.get("execution_mode") != "openlca_ipc":
            raise ValueError("openLCA execution mode is unsupported")
        if scientific.get("provider_connections") != native.get(
            "provider_connections"
        ):
            raise ValueError("openLCA provider graph drifted")
        method = scientific.get("impact_method")
        if (
            not isinstance(method, Mapping)
            or method.get("id") != method_spec.get("id")
            or method.get("version") != method_spec.get("version")
            or method.get("reference_unit") != method_spec.get("reference_unit")
        ):
            raise ValueError("openLCA method identity or unit drifted")
        by_suffix = {Path(path).name: value for path, value in documents.items()}
        flow_models: dict[tuple[str, str], dict] = {}
        process_models = []
        processes = scientific.get("processes")
        if not isinstance(processes, list) or not processes:
            raise ValueError("openLCA source-bound processes are absent")
        provider_by_exchange = {
            (row["consumer_process_id"], row["flow_id"]): row[
                "provider_process_id"
            ]
            for row in scientific["provider_connections"]  # type: ignore[index]
        }
        for process_ref in processes:
            if not isinstance(process_ref, Mapping):
                raise ValueError("openLCA process reference is invalid")
            process_id = str(process_ref["process_id"])
            version = str(process_ref["version"])
            source = by_suffix.get(f"{process_id}_{version}.json")
            if not isinstance(source, Mapping) or not isinstance(
                source.get("processDataSet"), Mapping
            ):
                raise ValueError("openLCA exact process source is absent")
            dataset = source["processDataSet"]
            info = dataset["processInformation"]
            stored_id = info["dataSetInformation"]["common:UUID"]
            if stored_id != process_id:
                raise ValueError("openLCA process source identity drifted")
            reference = str(
                info["quantitativeReference"]["referenceToReferenceFlow"]
            )
            exchanges_value = dataset["exchanges"]["exchange"]
            exchanges = (
                exchanges_value
                if isinstance(exchanges_value, list)
                else [exchanges_value]
            )
            compiled_exchanges = []
            for exchange in exchanges:
                flow_ref = exchange["referenceToFlowDataSet"]
                flow_id = str(flow_ref["@refObjectId"])
                flow_version = str(flow_ref.get("@version") or "")
                flow_source = by_suffix.get(f"{flow_id}_{flow_version}.json")
                if not isinstance(flow_source, Mapping) or not isinstance(
                    flow_source.get("flowDataSet"), Mapping
                ):
                    raise ValueError("openLCA exact flow source is absent")
                flow_key = (flow_id, flow_version)
                if flow_key not in flow_models:
                    flow_models[flow_key] = self._compile_flow(
                        flow_source["flowDataSet"], flow_id, flow_version
                    )
                direction = str(exchange["exchangeDirection"]).upper()
                compiled_exchanges.append(
                    {
                        "internal_id": int(exchange["@dataSetInternalID"]),
                        "flow_id": flow_id,
                        "flow_version": flow_version,
                        "amount": float(exchange["resultingAmount"]),
                        "is_input": direction == "INPUT",
                        "is_quantitative_reference": (
                            str(exchange["@dataSetInternalID"]) == reference
                        ),
                        "default_provider_id": provider_by_exchange.get(
                            (process_id, flow_id)
                        ),
                    }
                )
            process_models.append(
                {
                    "id": process_id,
                    "version": version,
                    "name": _tidas_text(
                        info["dataSetInformation"].get("common:name")
                        or info["dataSetInformation"].get("name", {}).get(
                            "baseName"
                        )
                    ),
                    "exchanges": compiled_exchanges,
                }
            )
        root = native.get("root_process")
        if not isinstance(root, Mapping):
            raise ValueError("openLCA root process is absent")
        factors = []
        for row in method_spec.get("factors") or []:
            if not isinstance(row, Mapping):
                raise ValueError("openLCA impact factor is invalid")
            factors.append(
                {
                    "flow_id": row["flow_id"],
                    "flow_version": row["flow_version"],
                    "value": float(row["value"]),
                }
            )
        category_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"impact-category:{method_spec['id']}@{method_spec['version']}",
            )
        )
        return {
            "contract": "lca_research_kernel.openlca_isolated_model/v1",
            "scientific_inputs_sha256": canonical_sha256(scientific),
            "flows": [
                flow_models[key] for key in sorted(flow_models)
            ],
            "processes": sorted(process_models, key=lambda row: row["id"]),
            "root_process": {
                "id": root["id"],
                "version": root["version"],
                "amount": float(root["reference_amount"]),
                "flow_id": root["reference_flow_id"],
                "unit": root["reference_unit"],
            },
            "impact_method": {
                "id": method_spec["id"],
                "version": method_spec["version"],
                "name": method_spec["name"],
                "reference_unit": method_spec["reference_unit"],
                "impact_category_id": category_id,
                "factors": factors,
            },
        }

    def _compile_flow(
        self, dataset: Mapping[str, object], flow_id: str, version: str
    ) -> dict:
        info = dataset["flowInformation"]  # type: ignore[index]
        if info["dataSetInformation"]["common:UUID"] != flow_id:  # type: ignore[index]
            raise ValueError("openLCA flow source identity drifted")
        reference = str(
            info["quantitativeReference"]["referenceToReferenceFlowProperty"]  # type: ignore[index]
        )
        properties_value = dataset["flowProperties"]["flowProperty"]  # type: ignore[index]
        properties = (
            properties_value
            if isinstance(properties_value, list)
            else [properties_value]
        )
        selected = next(
            row for row in properties if str(row["@dataSetInternalID"]) == reference
        )
        property_ref = selected["referenceToFlowPropertyDataSet"]
        property_id = str(property_ref["@refObjectId"])
        property_name = _tidas_text(property_ref.get("common:shortDescription"))
        lowered = property_name.casefold()
        reference_unit = (
            "kg" if "mass" in lowered else "m3" if "volume" in lowered else "reference-unit"
        )
        flow_type = str(
            dataset["modellingAndValidation"]["LCIMethod"]["typeOfDataSet"]  # type: ignore[index]
        )
        normalized_type = {
            "Product flow": "PRODUCT_FLOW",
            "Waste flow": "WASTE_FLOW",
            "Elementary flow": "ELEMENTARY_FLOW",
        }.get(flow_type)
        if normalized_type is None:
            raise ValueError("openLCA flow type is unsupported")
        return {
            "id": flow_id,
            "version": version,
            "name": _tidas_text(
                info["dataSetInformation"].get("common:name")  # type: ignore[index]
                or info["dataSetInformation"].get("name", {}).get("baseName")  # type: ignore[index,union-attr]
            ),
            "flow_type": normalized_type,
            "flow_property_id": property_id,
            "flow_property_name": property_name,
            "reference_unit": reference_unit,
            "unit_id": str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"unit:{property_id}")
            ),
            "unit_group_id": str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"unit-group:{property_id}")
            ),
        }

    def _execute_runtime(self, model: dict, runtime_root: Path) -> dict:
        descriptor = self.describe()
        node = shutil.which("node")
        if node is None:
            raise CapabilityGatewayError(
                "runtime_unavailable",
                "pinned Node runtime is unavailable",
                retryable=False,
                next_route="restore-pinned-node-runtime",
            )
        version = subprocess.run(
            [node, "--version"],
            shell=False,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if version.returncode != 0 or version.stdout.decode().strip() != "v24.18.0":
            raise CapabilityGatewayError(
                "source_drift",
                "Node runtime version drifted",
                retryable=False,
                next_route="restore-pinned-node-runtime",
            )
        template = self.runtime_root / "openlca-data/databases/d18-runtime-empty"
        data_root = runtime_root / "openlca-data"
        database = "controlled-calculation"
        database_root = data_root / "databases" / database
        if not database_root.exists():
            database_root.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(template, database_root)
        request_path = runtime_root / "request.json"
        request_path.write_text(
            json.dumps(
                model,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            candidate.bind(("127.0.0.1", 0))
            port = int(candidate.getsockname()[1])
        server = OpenLcaServer(self.runtime_root, port=port)
        server.start = lambda timeout_seconds=60: self._start_calculation_server(  # type: ignore[method-assign]
            server,
            data_root=data_root,
            database=database,
            timeout_seconds=timeout_seconds,
        )
        bridge = self.repository_root / (
            "runtime/tiangong-node/run-openlca-calculation.mjs"
        )
        module = self.runtime_root / "node/node_modules/olca-ipc/esm/mod.js"
        try:
            with server:
                completed = subprocess.run(
                    [
                        node,
                        str(bridge),
                        "--endpoint",
                        f"http://127.0.0.1:{port}",
                        "--module",
                        str(module),
                        "--request",
                        str(request_path),
                    ],
                    shell=False,
                    capture_output=True,
                    check=False,
                    timeout=int(descriptor["timeout_seconds"]),
                    env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "")},
                )
        except subprocess.TimeoutExpired as error:
            raise CapabilityGatewayError(
                "timeout",
                "isolated openLCA calculation timed out",
                retryable=True,
                next_route="retry-same-openlca-request",
            ) from error
        if completed.returncode != 0:
            raise CapabilityGatewayError(
                "openlca_calculation_failure",
                "isolated openLCA calculation failed",
                retryable=True,
                next_route="repair-or-retry-openlca-runtime",
            )
        try:
            value = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise CapabilityGatewayError(
                "invalid_capability_result",
                "isolated openLCA output is invalid",
                retryable=False,
                next_route="repair-openlca-runtime-adapter",
            ) from error
        if not isinstance(value, dict):
            raise ValueError("isolated openLCA output must be an object")
        return value

    def _start_calculation_server(
        self,
        server: OpenLcaServer,
        *,
        data_root: Path,
        database: str,
        timeout_seconds: int,
    ) -> None:
        if server.process is not None:
            raise ValueError("openLCA calculation server is already started")
        server.process = subprocess.Popen(
            compile_openlca_calculation_server_argv(
                self.runtime_root,
                data_root=data_root,
                database=database,
                port=server.port,
            ),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=data_root.parent,
            env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "")},
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if server.process.poll() is not None:
                raise RuntimeError(
                    "openLCA calculation server exited before readiness"
                )
            try:
                with socket.create_connection(
                    ("127.0.0.1", server.port), timeout=0.25
                ):
                    return
            except OSError:
                time.sleep(0.1)
        server.stop()
        raise TimeoutError("openLCA calculation server readiness timed out")
