"""Pinned production-model runtime loader and factory."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Callable, Mapping, Optional

from .model_credentials import (
    build_credential_provider,
    resolve_model_id,
    validate_credential_binding,
    validate_model_binding,
)
from .model_gateway import ModelGateway
from .model_schema_profiles import generic_source_candidate_schema
from .model_provider_adapters import (
    AnthropicSDKAdapter,
    GeminiSDKAdapter,
    OpenAISDKAdapter,
    ZhipuSDKAdapter,
    provider_runtime_profiles,
    validate_structured_output,
)

POLICY_PATH = Path("lca_research_kernel/contracts/model-runtime-policy.json")
SOURCE_LOCK_PATH = Path("runtime/model-python/source-lock.json")
PROVIDER_CATALOG_PATH = Path(
    "lca_research_kernel/contracts/model-provider-catalog.json"
)
POLICY_CONTRACT = "lca_research_kernel.model_runtime_policy/v1"
SOURCE_LOCK_CONTRACT = "lca_research_kernel.model_runtime_source_lock/v1"
PROVIDER_CATALOG_CONTRACT = "lca_research_kernel.model_provider_catalog/v1"
ADAPTER_TYPES = {
    "openai_official_sdk": OpenAISDKAdapter,
    "gemini_official_sdk": GeminiSDKAdapter,
    "anthropic_official_sdk": AnthropicSDKAdapter,
    "zhipu_official_sdk": ZhipuSDKAdapter,
}


class ModelRuntimeConfigurationError(ValueError):
    """The production runtime is not bound to an exact valid configuration."""


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelRuntimeConfigurationError(
            f"model runtime contract is unreadable: {path}"
        ) from error
    if not isinstance(value, dict):
        raise ModelRuntimeConfigurationError("model runtime contract must be an object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _exact_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelRuntimeConfigurationError(f"{field} is absent")
    lowered = value.lower()
    if "latest" in lowered or value in {"HEAD", "main", "master"}:
        raise ModelRuntimeConfigurationError(f"{field} is floating")
    return value


def _self_hash(value: Mapping[str, object], field: str) -> bool:
    expected = value.get(field)
    payload = {
        key: deepcopy(item) for key, item in value.items() if key != field
    }
    return isinstance(expected, str) and expected == _semantic_sha256(payload)


def _evidence_artifact(
    root: Path,
    pointer: object,
    *,
    label: str,
    json_object: bool = True,
) -> tuple[Path, Optional[dict]]:
    if (
        not isinstance(pointer, Mapping)
        or set(pointer) != {"path", "sha256"}
        or not isinstance(pointer.get("path"), str)
        or not isinstance(pointer.get("sha256"), str)
        or len(pointer["sha256"]) != 64
    ):
        raise ModelRuntimeConfigurationError(f"{label} pointer is invalid")
    relative = Path(pointer["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ModelRuntimeConfigurationError(f"{label} path escapes the repository")
    path = root / relative
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise ModelRuntimeConfigurationError(f"{label} is unavailable") from error
    if path.is_symlink() or not path.is_file() or _sha256(path) != pointer["sha256"]:
        raise ModelRuntimeConfigurationError(f"{label} drifted")
    value = _read_json(path) if json_object else None
    return path, value


def _validate_schema_profile_evidence(
    root: Path,
    *,
    route: Mapping[str, object],
    profile_id: str,
    schema: Mapping[str, object],
    evidence: object,
) -> None:
    if (
        not isinstance(evidence, Mapping)
        or set(evidence)
        != {"schema_sha256", "probe_receipt", "independent_review"}
        or evidence.get("schema_sha256") != schema.get("schema_sha256")
    ):
        raise ModelRuntimeConfigurationError(
            f"model route {route.get('route_id')} profile evidence is invalid"
        )
    _, receipt = _evidence_artifact(
        root, evidence["probe_receipt"], label="model schema-profile probe"
    )
    _, review = _evidence_artifact(
        root,
        evidence["independent_review"],
        label="model schema-profile independent review",
    )
    if (
        receipt.get("contract")
        != "lca_research_kernel.model_schema_profile_probe_receipt/v1"
        or receipt.get("status") != "accepted"
        or receipt.get("profile_id") != profile_id
        or receipt.get("provider_id") != route.get("provider_id")
        or receipt.get("adapter_id") != route.get("route_id")
        or receipt.get("schema_sha256") != schema.get("schema_sha256")
        or receipt.get("structured_output_valid") is not True
        or receipt.get("tool_requests") != 0
        or receipt.get("external_writes") != 0
        or receipt.get("scientific_result_created") is not False
        or receipt.get("sealed_reference_reads") != 0
        or not _self_hash(receipt, "receipt_sha256")
    ):
        raise ModelRuntimeConfigurationError(
            f"model route {route.get('route_id')} profile probe is not admission-grade"
        )
    raw_pointer = receipt.get("raw_baseline_acceptance")
    _, raw_acceptance = _evidence_artifact(
        root, raw_pointer, label="model schema-profile raw acceptance"
    )
    observed_count = raw_acceptance.get("model_observation", {}).get(
        "completed_call_count"
    )
    if (
        raw_acceptance.get("status") != "accepted_bounded_blind_baseline"
        or raw_acceptance.get("run_trace_observation", {}).get(
            "external_write_count"
        )
        != 0
        or raw_acceptance.get("run_trace_observation", {}).get(
            "sealed_reference_read_count"
        )
        != 0
        or receipt.get("observed_successful_call_count") != observed_count
        or not isinstance(observed_count, int)
        or observed_count < 1
    ):
        raise ModelRuntimeConfigurationError(
            "model schema-profile baseline does not prove a safe real call"
        )
    refs = receipt.get("validated_event_refs")
    if not isinstance(refs, list) or not refs:
        raise ModelRuntimeConfigurationError(
            "model schema-profile event evidence is absent"
        )
    if profile_id == "generic_source_candidate_v1":
        current_schema = generic_source_candidate_schema()
    else:
        from .model_schema_profiles import profile_probe_spec

        current_schema = profile_probe_spec(profile_id)["output_schema"]
    if _semantic_sha256(current_schema) != schema.get("schema_sha256"):
        raise ModelRuntimeConfigurationError(
            "model schema-profile contract drifted"
        )
    for ref in refs:
        trace_path, _ = _evidence_artifact(
            root,
            (
                {
                    "path": ref.get("path"),
                    "sha256": ref.get("file_sha256"),
                }
                if isinstance(ref, Mapping)
                else None
            ),
            label="model schema-profile Run Trace",
            json_object=False,
        )
        event = None
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            candidate = json.loads(line)
            if (
                isinstance(ref, Mapping)
                and candidate.get("event_id") == ref.get("event_id")
                and candidate.get("event_sha256") == ref.get("event_sha256")
            ):
                event = candidate
                break
        candidate = (
            event.get("payload", {}).get("candidate")
            if isinstance(event, Mapping)
            else None
        )
        if (
            not isinstance(event, Mapping)
            or event.get("event_type") != "research.run.model.completed"
            or not isinstance(candidate, Mapping)
            or candidate.get("provider") != route.get("provider_id")
            or candidate.get("adapter_id") != route.get("route_id")
            or candidate.get("status") != "candidate_only"
            or candidate.get("tool_requests") != []
            or candidate.get("model") != receipt.get("actual_model")
        ):
            raise ModelRuntimeConfigurationError(
                "model schema-profile Run Trace event is invalid"
            )
        try:
            validate_structured_output(candidate.get("output"), current_schema)
        except Exception as error:
            raise ModelRuntimeConfigurationError(
                "model schema-profile output is invalid"
            ) from error
    if (
        review.get("contract")
        != "lca_research_kernel.model_schema_profile_admission_review/v1"
        or review.get("status") != "passed"
        or review.get("profile_id") != profile_id
        or review.get("provider_id") != route.get("provider_id")
        or review.get("schema_sha256") != schema.get("schema_sha256")
        or review.get("probe_receipt_sha256")
        != receipt.get("receipt_sha256")
        or review.get("p1") != 0
        or review.get("p2") != 0
        or review.get("external_effects") != 0
        or not _self_hash(review, "review_sha256")
    ):
        raise ModelRuntimeConfigurationError(
            "model schema-profile independent review is not acceptance-grade"
        )


def load_model_runtime_contract(root: Path) -> dict:
    """Load and fail closed on a drifted SDK source lock or policy."""

    root = Path(root).resolve()
    policy = _read_json(root / POLICY_PATH)
    source_lock = _read_json(root / SOURCE_LOCK_PATH)
    catalog_pointer = policy.get("provider_catalog")
    if (
        not isinstance(catalog_pointer, Mapping)
        or catalog_pointer.get("path") != str(PROVIDER_CATALOG_PATH)
    ):
        raise ModelRuntimeConfigurationError(
            "model provider catalog pointer is invalid"
        )
    provider_catalog = _read_json(root / PROVIDER_CATALOG_PATH)
    if policy.get("contract") != POLICY_CONTRACT:
        raise ModelRuntimeConfigurationError("model runtime policy contract is invalid")
    if source_lock.get("contract") != SOURCE_LOCK_CONTRACT:
        raise ModelRuntimeConfigurationError("model source-lock contract is invalid")
    if provider_catalog.get("contract") != PROVIDER_CATALOG_CONTRACT:
        raise ModelRuntimeConfigurationError("model provider catalog is invalid")
    requirements = source_lock.get("requirements_lock")
    sources = source_lock.get("sdk_sources")
    if not isinstance(requirements, Mapping) or not isinstance(sources, Mapping):
        raise ModelRuntimeConfigurationError("model source lock is incomplete")
    if set(sources) != {
        "openai",
        "google-genai",
        "anthropic",
        "zai-sdk",
    }:
        raise ModelRuntimeConfigurationError(
            "model SDK sources must preserve OpenAI, Google Gen AI, "
            "Anthropic, and Z.AI"
        )
    python_contract = source_lock.get("python")
    if (
        not isinstance(python_contract, Mapping)
        or python_contract.get("requires") != ">=3.10"
        or not isinstance(python_contract.get("bootstrap_candidates"), list)
    ):
        raise ModelRuntimeConfigurationError("model runtime Python lock is invalid")
    requirements_path = root / _exact_text(
        requirements.get("path"), field="requirements lock path"
    )
    expected_requirements_sha = _exact_text(
        requirements.get("sha256"), field="requirements lock sha256"
    )
    if (
        not requirements_path.is_file()
        or _sha256(requirements_path) != expected_requirements_sha
    ):
        raise ValueError("requirements lock drifted")
    providers = provider_catalog.get("providers")
    schema_profiles = provider_catalog.get("schema_profiles")
    runtime_profiles = provider_catalog.get("runtime_profiles")
    admission_states = provider_catalog.get("admission_states")
    validation_gate = provider_catalog.get("current_validation_gate")
    admission_policy = provider_catalog.get("admission_policy")
    if (
        not isinstance(providers, list)
        or not providers
        or not isinstance(schema_profiles, Mapping)
        or not isinstance(runtime_profiles, Mapping)
        or runtime_profiles != provider_runtime_profiles()
        or set(schema_profiles)
        != {
            "generic_source_candidate_v1",
            "research_turn_intent_v2",
            "research_turn_intent_v3",
            "research_turn_semantics_v1",
            "material_answer_v2",
        }
        or not isinstance(admission_states, list)
        or set(admission_states)
        != {"adapter_ready_not_verified", "active_verified", "suspended"}
        or not isinstance(validation_gate, list)
        or not validation_gate
        or not isinstance(admission_policy, Mapping)
        or admission_policy.get("production_requires_active_verified") is not True
        or not isinstance(
            admission_policy.get(
                "capacity_qualification_compatible_catalog_sha256s"
            ),
            list,
        )
        or any(
            not isinstance(digest, str) or len(digest) != 64
            for digest in admission_policy[
                "capacity_qualification_compatible_catalog_sha256s"
            ]
        )
    ):
        raise ModelRuntimeConfigurationError(
            "model provider catalog admission contract is invalid"
        )
    for profile_id, profile in schema_profiles.items():
        if (
            not isinstance(profile, Mapping)
            or set(profile)
            != {"contract", "profile_id", "schema_sha256"}
            or profile.get("contract")
            != "lca_research_kernel.model_schema_profile/v1"
            or profile.get("profile_id") != profile_id
            or not isinstance(profile.get("schema_sha256"), str)
            or len(profile["schema_sha256"]) != 64
        ):
            raise ModelRuntimeConfigurationError(
                "model schema-profile catalog is invalid"
            )
    from .model_schema_profiles import profile_probe_spec

    for profile_id in (
        "research_turn_semantics_v1",
        "research_turn_intent_v2",
        "research_turn_intent_v3",
        "material_answer_v2",
    ):
        if (
            schema_profiles[profile_id]["schema_sha256"]
            != profile_probe_spec(profile_id)["schema_sha256"]
        ):
            raise ModelRuntimeConfigurationError(
                "research-intelligence schema-profile hash drifted"
            )
    seen_routes = set()
    seen_providers = set()
    required_gate = []
    for route in providers:
        if not isinstance(route, Mapping):
            raise ModelRuntimeConfigurationError("model route is invalid")
        route_id = _exact_text(route.get("route_id"), field="model route id")
        provider_id = _exact_text(
            route.get("provider_id"), field="model provider id"
        )
        runtime_profile_id = _exact_text(
            route.get("runtime_profile_id"),
            field="model provider runtime profile id",
        )
        runtime_profile = runtime_profiles.get(runtime_profile_id)
        if (
            not isinstance(runtime_profile, Mapping)
            or runtime_profile.get("provider_id") != provider_id
        ):
            raise ModelRuntimeConfigurationError(
                f"model route {route_id} runtime profile is invalid"
            )
        if route_id in seen_routes or provider_id in seen_providers:
            raise ModelRuntimeConfigurationError("model route IDs are duplicated")
        seen_routes.add(route_id)
        seen_providers.add(provider_id)
        adapter_type = _exact_text(
            route.get("adapter_type"), field="model adapter type"
        )
        if adapter_type not in ADAPTER_TYPES:
            raise ModelRuntimeConfigurationError(
                f"model adapter type is unavailable: {adapter_type}"
            )
        distribution = _exact_text(
            route.get("sdk_distribution"), field="model SDK distribution"
        )
        version = _exact_text(route.get("sdk_version"), field="model SDK version")
        source = sources.get(distribution)
        if (
            not isinstance(source, Mapping)
            or source.get("version") != version
            or source.get("distribution") != distribution
        ):
            raise ModelRuntimeConfigurationError(
                f"model route {route_id} does not match its adopted SDK source"
            )
        _exact_text(source.get("wheel_sha256"), field="model SDK wheel sha256")
        try:
            validate_model_binding(route.get("model_binding"))
            validate_credential_binding(route.get("credential_binding"))
        except ValueError as error:
            raise ModelRuntimeConfigurationError(
                f"model route {route_id} binding is invalid"
            ) from error
        _exact_text(route.get("network_host"), field="provider network host")
        _exact_text(route.get("base_url"), field="provider base URL")
        capabilities = route.get("capabilities")
        if not isinstance(capabilities, Mapping) or not all(
            capabilities.get(key) is True
            for key in ("structured_output", "streaming", "tool_requests")
        ):
            raise ModelRuntimeConfigurationError(
                f"model route {route_id} capability contract is incomplete"
            )
        profile_evidence = route.get("capability_verified_evidence")
        declared_profiles = {
            profile_id: capabilities.get(profile_id)
            for profile_id in schema_profiles
        }
        if (
            any(not isinstance(enabled, bool) for enabled in declared_profiles.values())
            or not isinstance(profile_evidence, Mapping)
        ):
            raise ModelRuntimeConfigurationError(
                f"model route {route_id} schema-profile declaration is invalid"
            )
        enabled_profiles = {
            profile_id
            for profile_id, enabled in declared_profiles.items()
            if enabled
        }
        if set(profile_evidence) != enabled_profiles:
            raise ModelRuntimeConfigurationError(
                f"model route {route_id} schema-profile evidence is absent"
            )
        for profile_id in enabled_profiles:
            _validate_schema_profile_evidence(
                root,
                route=route,
                profile_id=profile_id,
                schema=schema_profiles[profile_id],
                evidence=profile_evidence[profile_id],
            )
        if route.get("admission_state") not in admission_states:
            raise ModelRuntimeConfigurationError(
                f"model route {route_id} admission state is invalid"
            )
        evidence = route.get("active_verified_evidence")
        if route.get("admission_state") == "active_verified":
            if (
                not isinstance(evidence, list)
                or len(evidence) != 1
                or not isinstance(evidence[0], Mapping)
                or any(
                    not isinstance(evidence[0].get(field), str)
                    or len(evidence[0][field]) != 64
                    for field in (
                        "probe_receipt_sha256",
                        "independent_review_sha256",
                        "admitted_from_catalog_sha256",
                    )
                )
            ):
                raise ModelRuntimeConfigurationError(
                    f"active model route {route_id} lacks admission evidence"
                )
        elif evidence != []:
            raise ModelRuntimeConfigurationError(
                f"unverified model route {route_id} has active evidence"
            )
        if route.get("current_validation_required") is True:
            required_gate.append(provider_id)
    if required_gate != validation_gate:
        raise ModelRuntimeConfigurationError(
            "model provider validation gate does not match catalog entries"
        )
    output_policy = policy.get("output_policy")
    if (
        not isinstance(output_policy, Mapping)
        or output_policy.get("classification") != "candidate_only"
        or output_policy.get("project_record_direct_write") is not False
        or output_policy.get("secret_persistence_forbidden") is not True
    ):
        raise ModelRuntimeConfigurationError("model output policy is unsafe")
    return {
        **deepcopy(source_lock),
        "policy": deepcopy(policy),
        "provider_catalog": deepcopy(provider_catalog),
        "policy_sha256": _sha256(root / POLICY_PATH),
        "source_lock_sha256": _sha256(root / SOURCE_LOCK_PATH),
        "provider_catalog_sha256": _semantic_sha256(provider_catalog),
        "provider_catalog_file_sha256": _sha256(root / PROVIDER_CATALOG_PATH),
    }


def build_model_gateway(
    root: Path,
    environ: Mapping[str, str],
    *,
    execution_mode: str = "production",
    provider_ids: Optional[set[str]] = None,
    capacity_qualification: Optional[Mapping[str, object]] = None,
    credential_provider_factory: Callable[
        [Mapping[str, object], Mapping[str, str]], Callable[[], str]
    ] = build_credential_provider,
    clock: Callable[[], str] = lambda: "1970-01-01T00:00:00Z",
    sleep: Optional[Callable[[float], None]] = None,
) -> ModelGateway:
    """Build catalog-admitted official-SDK routes without reading credentials."""

    contract = load_model_runtime_contract(root)
    policy = contract["policy"]
    catalog = contract["provider_catalog"]
    if execution_mode not in {"production", "verification_probe"}:
        raise ModelRuntimeConfigurationError("model gateway execution mode is invalid")
    if provider_ids is not None and execution_mode != "verification_probe":
        raise ModelRuntimeConfigurationError(
            "provider subset is allowed only for verification probes"
        )
    if capacity_qualification is not None and execution_mode != "production":
        raise ModelRuntimeConfigurationError(
            "capacity qualification is allowed only for production routing"
        )
    if execution_mode == "production":
        eligible = [
            route
            for route in catalog["providers"]
            if route["admission_state"] == "active_verified"
        ]
        if not eligible:
            raise ModelRuntimeConfigurationError(
                "no active_verified model provider route is available"
            )
    else:
        gate = set(catalog["current_validation_gate"])
        selected = gate if provider_ids is None else set(provider_ids)
        if not selected or not selected.issubset(gate):
            raise ModelRuntimeConfigurationError(
                "verification provider subset is outside the current gate"
            )
        eligible = [
            route
            for route in catalog["providers"]
            if route["provider_id"] in selected
            and route.get("probe_eligible") is True
            and route["admission_state"]
            in {"adapter_ready_not_verified", "active_verified"}
        ]
        if {route["provider_id"] for route in eligible} != selected:
            raise ModelRuntimeConfigurationError(
                "verification provider subset contains an ineligible route"
            )
    eligible.sort(key=lambda row: (row["priority"], row["route_id"]))
    qualification_sha256 = None
    if capacity_qualification is not None:
        from .provider_capacity import (
            validate_provider_capacity_qualification,
        )

        try:
            qualification = validate_provider_capacity_qualification(
                capacity_qualification
            )
        except ValueError as error:
            raise ModelRuntimeConfigurationError(
                "model provider capacity qualification is invalid"
            ) from error
        if qualification["status"] != "qualified":
            raise ModelRuntimeConfigurationError(
                "model provider capacity qualification is not qualified"
            )
        compatible_capacity_catalogs = {
            contract["provider_catalog_sha256"],
            *catalog["admission_policy"][
                "capacity_qualification_compatible_catalog_sha256s"
            ],
        }
        if qualification["provider_catalog_sha256"] not in (
            compatible_capacity_catalogs
        ):
            raise ModelRuntimeConfigurationError(
                "model provider capacity catalog binding drifted"
            )
        if (
            qualification["source_lock_sha256"]
            != contract["source_lock_sha256"]
        ):
            raise ModelRuntimeConfigurationError(
                "model provider capacity source lock binding drifted"
            )
        matches = [
            route
            for route in eligible
            if route["provider_id"] == qualification["provider_id"]
        ]
        if len(matches) != 1:
            raise ModelRuntimeConfigurationError(
                "qualified model provider is not uniquely active"
            )
        selected = matches[0]
        profile_id = qualification["schema_profile"]
        if (
            selected["sdk_version"] != qualification["sdk_version"]
            or selected["capabilities"].get(profile_id) is not True
            or qualification["adapter_capabilities"].get(profile_id)
            is not True
        ):
            raise ModelRuntimeConfigurationError(
                "qualified model provider capability binding drifted"
            )
        _evidence_artifact(
            Path(root).resolve(),
            qualification["probe_receipt_ref"],
            label="model provider capacity probe",
        )
        eligible = [
            selected,
            *[
                route
                for route in eligible
                if route["route_id"] != selected["route_id"]
            ],
        ]
        qualification_sha256 = qualification["receipt_sha256"]
    declared = {route["route_id"]: route for route in eligible}
    route_order = [route["route_id"] for route in eligible]
    adapters = {}
    runtime_routes = []
    for priority, route_id in enumerate(route_order, start=1):
        route = declared[route_id]
        try:
            model_id = resolve_model_id(route["model_binding"], environ)
            credential_provider = credential_provider_factory(
                route["credential_binding"], environ
            )
        except ValueError as error:
            raise ModelRuntimeConfigurationError(
                f"configured model ID or credential binding is absent for route "
                f"{route_id}"
            ) from error
        common = {
            "adapter_id": route_id,
            "model_id": model_id,
            "api_key_provider": credential_provider,
            "runtime_profile_id": route["runtime_profile_id"],
        }
        adapter_class = ADAPTER_TYPES.get(route["adapter_type"])
        if adapter_class is None:
            raise ModelRuntimeConfigurationError(
                f"model provider is unsupported for route {route_id}"
            )
        adapter = adapter_class(**common)
        adapter._catalog_capabilities = {  # type: ignore[attr-defined]
            **deepcopy(dict(route["capabilities"])),
            "sdk_version": route["sdk_version"],
            "admission_state": route["admission_state"],
        }
        if (
            adapter.provider_id != route["provider_id"]
            or adapter.network_host != route["network_host"]
            or adapter.base_url != route["base_url"]
        ):
            raise ModelRuntimeConfigurationError(
                f"model adapter identity drifted for route {route_id}"
            )
        adapters[route_id] = adapter
        runtime_routes.append(
            {
                "route_id": route_id,
                "adapter_id": route_id,
                "priority": priority,
            }
        )
    runtime_policy = {
        "contract": POLICY_CONTRACT,
        "policy_id": policy["policy_id"],
        "routes": runtime_routes,
        "retry": deepcopy(policy["retry"]),
        "circuit_breaker": deepcopy(policy["circuit_breaker"]),
        "source_lock_sha256": contract["source_lock_sha256"],
        "policy_sha256": contract["policy_sha256"],
        "provider_catalog_sha256": contract["provider_catalog_sha256"],
        "runtime_profiles_sha256": _semantic_sha256(
            catalog["runtime_profiles"]
        ),
        "execution_mode": execution_mode,
    }
    if qualification_sha256 is not None:
        runtime_policy["capacity_qualification_sha256"] = (
            qualification_sha256
        )
    keyword = {"sleep": sleep} if sleep is not None else {}
    return ModelGateway(
        adapters,
        policy=runtime_policy,
        clock=clock,
        **keyword,
    )


def build_production_model_gateway(
    root: Path,
    environ: Mapping[str, str],
    *,
    execution_mode: str = "production",
    provider_ids: Optional[set[str]] = None,
    capacity_qualification: Optional[Mapping[str, object]] = None,
    credential_provider_factory: Callable[
        [Mapping[str, object], Mapping[str, str]], Callable[[], str]
    ] = build_credential_provider,
    clock: Callable[[], str] = lambda: "1970-01-01T00:00:00Z",
    sleep: Optional[Callable[[float], None]] = None,
) -> ModelGateway:
    """Backward-compatible port backed by the provider catalog."""

    return build_model_gateway(
        root,
        environ,
        execution_mode=execution_mode,
        provider_ids=provider_ids,
        capacity_qualification=capacity_qualification,
        credential_provider_factory=credential_provider_factory,
        clock=clock,
        sleep=sleep,
    )
