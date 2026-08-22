"""Fail-closed production composition for the D-18 Product Interface."""

from __future__ import annotations

import json
import os
import re
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from lca_research_kernel.agent_bootstrap import (
    ADOPTED_STARTUP_MANIFEST_SHA256,
    AgentBootstrap,
)
from lca_research_kernel.assurance import AssuranceBoundary
from lca_research_kernel.capability_catalog import CapabilityCatalog
from lca_research_kernel.capability_gateway import CapabilityGateway
from lca_research_kernel.model_runtime import (
    ModelRuntimeConfigurationError,
    build_production_model_gateway,
    load_model_runtime_contract,
)
from lca_research_kernel.product_interface import ProductInterface
from lca_research_kernel.material_resolution import MaterialResolutionBinding
from lca_research_kernel.semantic_context import ResearchSemanticContext

from .runtime import MaterialResolver, ProductInterfaceRuntime
from .input_artifacts import InputArtifactStore
from .material_store import MaterialStore
from .native_evidence_authentication import (
    open_native_evidence_authentication_ports,
)
from .tiangong_native_adapter import TianGongTidasSdkContractAdapter

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SHA256_INTEGRITY = re.compile(r"^sha256-([0-9a-f]{64})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CAPACITY_QUALIFICATION_ENV = "D18_MODEL_CAPACITY_QUALIFICATION_FILE"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unavailable or invalid") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _capacity_qualification_from_environment(
    repository_root: Path,
    environ: Mapping[str, str],
) -> dict | None:
    value = environ.get(_CAPACITY_QUALIFICATION_ENV)
    if not value:
        return None
    root = Path(repository_root).resolve()
    path = Path(value).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(
            "model capacity qualification escaped the repository"
        ) from error
    return _read_json_object(path, "model capacity qualification")


def _manifest_bound_registry_paths(repository_root: Path) -> tuple[Path, Path]:
    """Hash startup-bound registries before either registry is parsed."""

    root = Path(repository_root).resolve()
    manifest = _read_json_object(
        root / "lca_research_kernel/contracts/agent-startup-manifest-v2.json",
        "agent startup manifest",
    )
    if (
        manifest.get("contract")
        != "lca_research_kernel.agent_startup_manifest/v1"
    ):
        raise ValueError("agent startup manifest contract is unsupported")
    resolved: dict[str, Path] = {}
    expected_paths = {
        "capability_catalog": (
            "lca_research_kernel/contracts/tiangong-capability-catalog.json"
        ),
        "source_lock": (
            "lca_research_kernel/contracts/tiangong-source-lock.json"
        ),
    }
    for field, expected_path in expected_paths.items():
        reference = manifest.get(field)
        if (
            not isinstance(reference, Mapping)
            or set(reference) != {"path", "sha256"}
            or reference.get("path") != expected_path
            or not isinstance(reference.get("sha256"), str)
            or _SHA256.fullmatch(reference["sha256"]) is None
        ):
            raise ValueError(f"agent startup manifest {field} binding is invalid")
        path = (root / expected_path).resolve()
        try:
            path.relative_to(root)
            content = path.read_bytes()
        except (OSError, ValueError) as error:
            raise ValueError(
                f"agent startup manifest {field} source is unavailable"
            ) from error
        if hashlib.sha256(content).hexdigest() != reference["sha256"]:
            raise ValueError(
                f"agent startup manifest {field} source drifted"
            )
        resolved[field] = path
    return resolved["capability_catalog"], resolved["source_lock"]


def _official_source_descriptor_from_json(repository_root: Path) -> dict:
    """Locate the protected source using only inert JSON before importing it."""

    contracts = Path(repository_root) / "lca_research_kernel/contracts"
    catalog = _read_json_object(
        contracts / "tiangong-capability-catalog.json",
        "production capability catalog",
    )
    source_lock = _read_json_object(
        contracts / "tiangong-source-lock.json",
        "production capability source lock",
    )
    capabilities = catalog.get("capabilities")
    sources = source_lock.get("sources")
    if (
        catalog.get("contract")
        != "lca_research_kernel.capability_catalog/v1"
        or source_lock.get("contract")
        != "lca_research_kernel.source_lock/v1"
        or not isinstance(capabilities, list)
        or not isinstance(sources, list)
    ):
        raise ValueError("production capability source registry is invalid")
    matches = [
        row
        for row in capabilities
        if isinstance(row, Mapping)
        and row.get("capability_id") == "official-public-source-read"
    ]
    if len(matches) != 1:
        raise ValueError("official public source capability is not uniquely registered")
    capability = matches[0]
    if (
        capability.get("adapter_id") != "official_public_source"
        or capability.get("entrypoint")
        != "OfficialPublicSourceCapability.execute"
    ):
        raise ValueError("official public source capability identity drifted")
    source_id = capability.get("source_lock_id")
    matched_sources = [
        row
        for row in sources
        if isinstance(row, Mapping) and row.get("source_id") == source_id
    ]
    if len(matched_sources) != 1:
        raise ValueError("official public source lock is not uniquely registered")
    source = dict(matched_sources[0])
    if (
        source.get("identity")
        != (
            "lca_research_kernel.official_public_source."
            "OfficialPublicSourceCapability"
        )
        or source.get("source_url")
        != "repo://lca_research_kernel/official_public_source.py"
    ):
        raise ValueError("official public source lock identity drifted")
    index_sources = [
        row
        for row in sources
        if isinstance(row, Mapping)
        and row.get("source_id")
        == "lca.kernel.official-public-source-index"
    ]
    if len(index_sources) != 1:
        raise ValueError("official public source index lock is not unique")
    index_source = dict(index_sources[0])
    if (
        index_source.get("identity")
        != "lca_research_kernel.official_public_source_index"
        or index_source.get("source_url")
        != (
            "repo://lca_research_kernel/contracts/"
            "official-public-source-index.json"
        )
    ):
        raise ValueError("official public source index identity drifted")
    return {
        "source_lock": source,
        "source_index_lock": index_source,
    }


def _verify_repo_source_lock(
    repository_root: Path, descriptor: Mapping[str, object]
) -> None:
    root = Path(repository_root).resolve()
    for key in ("source_lock", "source_index_lock"):
        source = descriptor.get(key)
        if not isinstance(source, Mapping):
            raise ValueError("production capability source lock is absent")
        source_url = source.get("source_url")
        integrity = source.get("integrity")
        if not isinstance(source_url, str) or not source_url.startswith("repo://"):
            raise ValueError("production capability source is not repository-bound")
        if not isinstance(integrity, str):
            raise ValueError("production capability integrity is absent")
        match = _SHA256_INTEGRITY.fullmatch(integrity)
        if match is None:
            raise ValueError("production capability integrity is unsupported")
        relative = Path(source_url.removeprefix("repo://"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("production capability source path is unsafe")
        source_path = (root / relative).resolve()
        try:
            source_path.relative_to(root)
        except ValueError as error:
            raise ValueError(
                "production capability source escaped repository"
            ) from error
        try:
            observed = hashlib.sha256(source_path.read_bytes()).hexdigest()
        except OSError as error:
            raise ValueError("production capability source is unreadable") from error
        if observed != match.group(1):
            raise ValueError("production capability source bytes drifted")


@dataclass(frozen=True)
class RuntimeBinding:
    runtime: ProductInterfaceRuntime | None
    status: str
    failure_code: str | None = None
    material_capability_snapshot: dict | None = None

    @property
    def ready(self) -> bool:
        return self.runtime is not None and self.status == "ready"


class _RuntimeBootstrapFailure(RuntimeError):
    """Internal phase marker; error bodies never cross the runtime boundary."""

    def __init__(self, failure_code: str):
        super().__init__(failure_code)
        self.failure_code = failure_code


class RuntimeFactory:
    """Build accepted gateways without retaining or reporting credential values."""

    def __init__(
        self,
        *,
        repository_root: Path,
        data_root: Path,
        environ: Mapping[str, str] | None = None,
        model_gateway: object = None,
        capacity_qualification: Mapping[str, object] | None = None,
        official_source_transport: object = None,
        run_id: str | None = None,
        run_budget: Mapping[str, object] | None = None,
        input_artifact_store: InputArtifactStore | None = None,
        material_store: MaterialStore | None = None,
        clock=_utc_now,
    ):
        self.repository_root = Path(repository_root).resolve()
        self.data_root = Path(data_root).resolve()
        self.environ = os.environ if environ is None else environ
        self.model_gateway = model_gateway
        self.capacity_qualification = capacity_qualification
        self.official_source_transport = official_source_transport
        self.run_id = run_id
        self.run_budget = None if run_budget is None else dict(run_budget)
        self.input_artifact_store = input_artifact_store
        self.material_store = material_store
        self.material_control_db = (
            material_store.path
            if material_store is not None
            else self.data_root / "control/materials.sqlite3"
        )
        self.material_input_root = self.data_root
        self.clock = clock

    def _build_capability_gateway_for_materials(self) -> CapabilityGateway:
        """Build a locked catalog-only gateway for material boundary tests."""

        catalog = CapabilityCatalog.from_files(
            self.repository_root
            / "lca_research_kernel/contracts/tiangong-capability-catalog.json",
            self.repository_root
            / "lca_research_kernel/contracts/tiangong-source-lock.json",
        )
        return CapabilityGateway(catalog, adapters={})

    def build(self) -> RuntimeBinding:
        material_store = self.material_store
        owns_material_store = False
        try:
            input_artifact_store = self.input_artifact_store
            if input_artifact_store is None:
                input_artifact_store = InputArtifactStore(
                    self.data_root, clock=self.clock
                )
            if material_store is None:
                material_store = MaterialStore(
                    self.material_control_db,
                    input_artifacts=input_artifact_store,
                    data_root=self.data_root,
                    clock=self.clock,
                )
                owns_material_store = True
            if material_store.input_artifacts is not input_artifact_store:
                raise ValueError("material stores must share one input artifact identity")
            run_id = self.run_id or self.environ.get("D18_SERVICE_RUN_ID")
            if run_id is not None and _RUN_ID.fullmatch(run_id) is None:
                raise ValueError("service run identity is invalid")
            try:
                startup_context = AgentBootstrap.load(
                    self.repository_root,
                    self.repository_root
                    / "lca_research_kernel/contracts/agent-startup-manifest-v2.json",
                    expected_manifest_sha256=ADOPTED_STARTUP_MANIFEST_SHA256,
                )
                _manifest_bound_registry_paths(self.repository_root)
                preimport_descriptor = _official_source_descriptor_from_json(
                    self.repository_root
                )
                _verify_repo_source_lock(
                    self.repository_root,
                    preimport_descriptor,
                )
            except (OSError, ValueError) as error:
                raise _RuntimeBootstrapFailure("source_lock_drift") from error
            from lca_research_kernel.official_public_source import (
                OfficialPublicSourceCapability,
            )

            gateway = self.model_gateway
            if gateway is None:
                try:
                    capacity_qualification = (
                        self.capacity_qualification
                        if self.capacity_qualification is not None
                        else _capacity_qualification_from_environment(
                            self.repository_root,
                            self.environ,
                        )
                    )
                    gateway = build_production_model_gateway(
                        self.repository_root,
                        self.environ,
                        capacity_qualification=capacity_qualification,
                        clock=self.clock,
                    )
                except (ModelRuntimeConfigurationError, OSError, ValueError) as error:
                    raise _RuntimeBootstrapFailure(
                        "provider_configuration_invalid"
                    ) from error
            catalog = CapabilityCatalog.from_files(
                self.repository_root
                / "lca_research_kernel/contracts/tiangong-capability-catalog.json",
                self.repository_root
                / "lca_research_kernel/contracts/tiangong-source-lock.json",
            )
            official_source = OfficialPublicSourceCapability(
                self.data_root / "control/official-public-source-cache",
                transport=self.official_source_transport,
                wall_clock=self.clock,
                source_index_path=(
                    self.repository_root
                    / "lca_research_kernel/contracts/"
                    "official-public-source-index.json"
                ),
            )
            tidas_sdk_contract = TianGongTidasSdkContractAdapter(
                self.repository_root / "runtime/tiangong-node"
            )
            native_evidence_authentication = (
                open_native_evidence_authentication_ports(self.data_root)
            )
            capability_gateway = CapabilityGateway(
                catalog,
                adapters={
                    "official_public_source": official_source,
                    "static.tidas-sdk": tidas_sdk_contract,
                },
                native_evidence_signer=native_evidence_authentication.sign,
                native_evidence_verifier=native_evidence_authentication.verify,
            )
            material_capability_snapshot = capability_gateway.material_routing_snapshot(
                (
                    "tiangong.validation.tidas",
                    "tiangong.tidas-tools.tidas-import",
                    "tiangong.calculation.openlca-ipc",
                )
            )
            semantic_context = ResearchSemanticContext.from_files(
                self.repository_root,
                source_lock_path=(
                    self.repository_root
                    / "lca_research_kernel/contracts/tiangong-source-lock.json"
                ),
                capability_catalog_path=(
                    self.repository_root
                    / "lca_research_kernel/contracts/tiangong-capability-catalog.json"
                ),
                skill_registry_path=(
                    self.repository_root
                    / "lca_research_kernel/contracts/research-skill-registry.json"
                ),
                domain_rule_registry_path=(
                    self.repository_root
                    / "lca_research_kernel/contracts/domain-rule-registry.json"
                ),
            )
            material_resolver = MaterialResolver(
                data_root=self.data_root,
                material_store=material_store,
                input_artifact_store=input_artifact_store,
            )

            def resolve_revisions(project_id, revision_refs):
                material_ids = []
                for reference in revision_refs:
                    material_id = reference.get("material_id")
                    if not isinstance(material_id, str) or not material_id:
                        raise ValueError("material revision identity is invalid")
                    material_ids.append(material_id)
                return material_resolver.resolve(
                    project_id, material_ids, revision_refs
                )

            material_resolution = MaterialResolutionBinding(
                verified_content_resolver=(
                    material_resolver.resolve_project_record_content
                ),
                material_revision_resolver=resolve_revisions,
            )
            product_interface = ProductInterface(
                self.data_root,
                model_gateway=gateway,
                capability_gateway=capability_gateway,
                assurance=AssuranceBoundary(),
                clock=self.clock,
                startup_context=startup_context,
                run_id=run_id,
                run_budget=self.run_budget,
                semantic_context=semantic_context,
                enable_artifact_workers=True,
                material_resolution_binding=material_resolution,
            )
            return RuntimeBinding(
                runtime=ProductInterfaceRuntime(
                    product_interface,
                    data_root=self.data_root,
                    input_artifact_store=input_artifact_store,
                    material_store=material_store,
                    owns_material_store=owns_material_store,
                    material_resolution_binding=material_resolution,
                ),
                status="ready",
                material_capability_snapshot=material_capability_snapshot,
            )
        except (
            OSError,
            ValueError,
            ModelRuntimeConfigurationError,
            _RuntimeBootstrapFailure,
        ) as error:
            if owns_material_store and material_store is not None:
                material_store.close()
            if isinstance(error, _RuntimeBootstrapFailure):
                failure_code = error.failure_code
            elif isinstance(error, ModelRuntimeConfigurationError):
                failure_code = "provider_configuration_invalid"
            elif isinstance(error, OSError):
                failure_code = "filesystem_runtime_unavailable"
            else:
                failure_code = "product_interface_runtime_unavailable"
            return RuntimeBinding(
                runtime=None,
                status="unavailable",
                failure_code=failure_code,
            )


def required_runtime_environment_names(repository_root: Path) -> tuple[str, ...]:
    """Return exact declared configuration names, never their values."""

    from lca_research_kernel.model_credentials import (
        credential_environment_name,
        validate_model_binding,
    )

    contract = load_model_runtime_contract(Path(repository_root).resolve())
    names: set[str] = set()
    for route in contract["provider_catalog"]["providers"]:
        if route["admission_state"] != "active_verified":
            continue
        credential_name = credential_environment_name(
            route["credential_binding"]
        )
        if credential_name is not None:
            names.add(credential_name)
        model_binding = validate_model_binding(route["model_binding"])
        if model_binding["kind"] == "environment":
            names.add(model_binding["name"])
    if any(not _ENVIRONMENT_NAME.fullmatch(name) for name in names):
        raise ModelRuntimeConfigurationError(
            "model runtime environment name is invalid"
        )
    return tuple(sorted(names))
