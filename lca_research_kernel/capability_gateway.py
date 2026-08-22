"""Authorized, source-preserving capability port for the D-18 runtime."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Callable, Mapping


REQUEST_CONTRACT = "lca_research_kernel.capability_request/v1"
NATIVE_EVIDENCE_EVENT_TYPE = "research.run.capability.native_evidence"
NATIVE_EVIDENCE_AUTHENTICATION_CONTRACT = (
    "lca_research_kernel.native_evidence_authentication/v1"
)


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class CapabilityGatewayError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        next_route: str,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.next_route = next_route


class LocalDeterministicCapability:
    def __init__(
        self,
        capability_id: str,
        executor: Callable[[dict], object],
        *,
        input_schema: Mapping[str, object] = None,
    ):
        self.capability_id = capability_id
        self.executor = executor
        self.input_schema = deepcopy(
            dict(input_schema)
            if isinstance(input_schema, Mapping)
            else {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            }
        )

    def describe(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "authority": "local_deterministic_execution",
            "access": "local_only",
            "effect": "local_read",
            "result_status": "sourced_result_not_fact",
            "input_contract": (
                "lca_research_kernel.local_deterministic_nonmaterial_input/v1"
            ),
            "material_input": False,
            "input_schema": deepcopy(self.input_schema),
        }

    def execute(
        self,
        payload: Mapping[str, object],
        *,
        execution_identity: Mapping[str, object] = None,
        operation_receipt: Mapping[str, object] = None,
    ) -> dict:
        return {
            "content": deepcopy(self.executor(deepcopy(dict(payload)))),
            "source_identity": self.capability_id,
            "source_version": "local-deterministic/v1",
        }


class TianGongReadOnlyCapability:
    def __init__(
        self,
        capability_id: str,
        reader: Callable[[dict], Mapping[str, object]],
        *,
        public_credential_free: bool = False,
        input_schema: Mapping[str, object] = None,
    ):
        self.capability_id = capability_id
        self.reader = reader
        self.public_credential_free = public_credential_free
        self.input_schema = deepcopy(
            dict(input_schema)
            if isinstance(input_schema, Mapping)
            else {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            }
        )

    def describe(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "authority": "TianGong",
            "access": "read_only",
            "effect": "external_read",
            "result_status": "sourced_result_not_fact",
            "identity_policy": "exact_identity_and_version_required",
            "public_credential_free": self.public_credential_free,
            "input_contract": (
                "lca_research_kernel.tiangong_read_only_nonmaterial_input/v1"
            ),
            "material_input": False,
            "input_schema": deepcopy(self.input_schema),
        }

    def execute(
        self,
        payload: Mapping[str, object],
        *,
        execution_identity: Mapping[str, object] = None,
        operation_receipt: Mapping[str, object] = None,
    ) -> dict:
        response = self.reader(deepcopy(dict(payload)))
        if not isinstance(response, Mapping):
            raise CapabilityGatewayError(
                "invalid_tiangong_response",
                "TianGong reader returned a non-object response",
                retryable=True,
                next_route="retry-or-switch-tiangong-read-route",
            )
        identity = response.get("identity")
        version = response.get("version")
        content = response.get("payload")
        if not all(isinstance(value, str) and value for value in (identity, version)):
            raise CapabilityGatewayError(
                "missing_tiangong_identity",
                "TianGong result lacks exact identity or version",
                retryable=False,
                next_route="retain-explicit-authority-gap",
            )
        if payload.get("process_id") and payload.get("process_id") != identity:
            raise CapabilityGatewayError(
                "tiangong_identity_drift",
                "TianGong result identity differs from the request",
                retryable=False,
                next_route="reject-drifted-tiangong-result",
            )
        if payload.get("version") and payload.get("version") != version:
            raise CapabilityGatewayError(
                "tiangong_version_drift",
                "TianGong result version differs from the request",
                retryable=False,
                next_route="reject-drifted-tiangong-result",
            )
        return {
            "content": deepcopy(content),
            "source_identity": identity,
            "source_version": version,
            "source": response.get("source"),
        }


class CapabilityGateway:
    def __init__(
        self,
        capabilities: object,
        *,
        adapters: Mapping[str, object] = None,
        execution_environment: object = None,
        remaining_budget_seconds: Callable[[], float] = None,
        answer_timing: object = None,
        native_evidence_signer: Callable[[bytes], str] = None,
        native_evidence_verifier: Callable[[bytes, str], bool] = None,
    ):
        self.catalog = None
        self.adapters_by_id = dict(adapters or {})
        self.execution_environment = execution_environment
        self.remaining_budget_seconds = remaining_budget_seconds
        self.answer_timing = answer_timing
        self.native_evidence_trace = None
        self.native_evidence_clock = None
        if (native_evidence_signer is None) != (native_evidence_verifier is None):
            raise ValueError("native evidence authentication ports are incomplete")
        if native_evidence_signer is not None and (
            not callable(native_evidence_signer)
            or not callable(native_evidence_verifier)
        ):
            raise ValueError("native evidence authentication ports are invalid")
        self._native_evidence_signer = native_evidence_signer
        self._native_evidence_verifier = native_evidence_verifier
        if hasattr(capabilities, "list_descriptors") and hasattr(
            capabilities, "snapshot_sha256"
        ):
            self.catalog = capabilities
            self.capabilities_by_id = {
                row["capability_id"]: row
                for row in capabilities.list_descriptors()  # type: ignore[attr-defined]
            }
        elif isinstance(capabilities, Mapping):
            self.capabilities_by_id = dict(capabilities)
            for capability_id, capability in self.capabilities_by_id.items():
                description = capability.describe()  # type: ignore[attr-defined]
                if description.get("capability_id") != capability_id:
                    raise ValueError("capability identity differs from its registry key")
        else:
            raise ValueError("capability registry is unsupported")

    def list_capabilities(self, context: Mapping[str, object]) -> list[dict]:
        if self.catalog is not None:
            rows = []
            for key in sorted(self.capabilities_by_id):
                row = self._public_description(self.capabilities_by_id[key])
                row["runtime_available"] = self._runtime_available(row)
                rows.append(row)
            return rows
        return [
            deepcopy(self.capabilities_by_id[key].describe())  # type: ignore[attr-defined]
            for key in sorted(self.capabilities_by_id)
        ]

    def material_routing_snapshot(self, capability_ids: tuple[str, ...]) -> dict:
        """Freeze serializable catalog metadata and explicit live adapter state."""

        if self.catalog is None:
            raise ValueError("material routing snapshot requires a locked catalog")
        requested = set(capability_ids)
        rows = [
            row
            for row in self.list_capabilities({"route": "material-worker"})
            if row.get("capability_id") in requested
        ]
        if {row["capability_id"] for row in rows} != requested:
            raise ValueError("material routing capability is absent from catalog")
        snapshot = {
            "contract": "lca_research_kernel.material_capability_snapshot/v1",
            "catalog_snapshot_sha256": self.catalog.snapshot_sha256,
            "capabilities": rows,
            "candidate_only": True,
        }
        snapshot["snapshot_sha256"] = canonical_sha256(snapshot)
        return snapshot

    def execute(
        self,
        capability_request: Mapping[str, object],
        *,
        pre_attempt: Callable[
            [Mapping[str, object], Mapping[str, object]], None
        ] = None,
    ) -> dict:
        request = self._validate_request(capability_request)
        capability_id = request["capability_id"]
        capability = self.capabilities_by_id.get(capability_id)
        if capability is None:
            raise CapabilityGatewayError(
                "capability_unavailable",
                f"capability is not registered: {capability_id}",
                retryable=False,
                next_route="select-another-capability",
            )
        if self.catalog is not None:
            description = deepcopy(capability)
            if request.get("catalog_snapshot_sha256") != self.catalog.snapshot_sha256:
                raise ValueError("capability catalog snapshot drifted")
            if request.get("source_lock_id") != description.get("source_lock_id"):
                raise ValueError("capability source lock drifted")
            if description.get("disposition") == "human_action_required":
                raise CapabilityGatewayError(
                    "human_action_required",
                    "capability is outside autonomous responsibility",
                    retryable=False,
                    next_route=str(description.get("next_route")),
                )
            adapter = self.adapters_by_id.get(str(description.get("adapter_id")))
            if adapter is None or not self._adapter_supports(adapter, description):
                raise CapabilityGatewayError(
                    "runtime_unavailable",
                    "cataloged capability runtime is unavailable",
                    retryable=False,
                    next_route=str(description.get("recovery_route")),
                )
        else:
            description = capability.describe()  # type: ignore[attr-defined]
            adapter = capability
        runtime_timeout = request.get("runtime_timeout_seconds")
        if runtime_timeout is not None:
            if (
                isinstance(runtime_timeout, bool)
                or not isinstance(runtime_timeout, (int, float))
                or runtime_timeout <= 0
            ):
                raise ValueError("capability runtime timeout is invalid")
            effective_timeout = float(runtime_timeout)
            if self.remaining_budget_seconds is not None:
                remaining = self._remaining_seconds()
                if remaining <= 0:
                    raise CapabilityGatewayError(
                        "capability_deadline_exhausted",
                        "no answer deadline remains for capability execution",
                        retryable=False,
                        next_route="return-bounded-conclusion-or-gap",
                    )
                effective_timeout = min(effective_timeout, remaining)
            if not hasattr(adapter, "with_runtime_timeout"):
                if self.remaining_budget_seconds is not None:
                    raise CapabilityGatewayError(
                        "capability_timeout_unsupported",
                        "capability adapter cannot honor the answer deadline",
                        retryable=False,
                        next_route="return-bounded-conclusion-or-gap",
                    )
            else:
                adapter = adapter.with_runtime_timeout(effective_timeout)
            if self.remaining_budget_seconds is not None:
                if not hasattr(adapter, "with_remaining_budget"):
                    raise CapabilityGatewayError(
                        "capability_timeout_unsupported",
                        "capability adapter cannot recompute the answer deadline",
                        retryable=False,
                        next_route="return-bounded-conclusion-or-gap",
                    )
                adapter = adapter.with_remaining_budget(
                    self.remaining_budget_seconds
                )
            if self.answer_timing is not None and hasattr(
                adapter, "with_answer_timing"
            ):
                adapter = adapter.with_answer_timing(self.answer_timing)
        if request["effect"] != description.get("effect"):
            raise ValueError("capability effect differs from its registered contract")
        material_binding = self._native_material_binding(request["input"])
        native_qualification = None
        if material_binding is not None:
            native_qualification = self._validate_native_material_adapter(
                adapter, description
            )
        execution_identity = request.get("execution_identity")
        operation_receipt = request.get("operation_receipt")
        network_authorization = None
        if self.execution_environment is not None:
            if not isinstance(execution_identity, Mapping) or not isinstance(
                operation_receipt, Mapping
            ):
                raise ValueError("capability execution identity or receipt is absent")
            self.execution_environment.validate_operation_receipt(
                operation_receipt,
                execution_identity,
                capability_id=capability_id,
                effect=str(request["effect"]),
                arguments=request["input"],
            )
            if request["effect"] == "external_read" and hasattr(
                adapter, "network_host_for"
            ):
                network_host = adapter.network_host_for(request["input"])  # type: ignore[attr-defined]
                network_authorization = self.execution_environment.authorize_network(
                    network_host,
                    "external_read",
                )
        if pre_attempt is not None:
            pre_attempt(
                deepcopy(request),
                {
                    "adapter_id": str(description.get("adapter_id", capability_id)),
                    "route_id": capability_id,
                    "attempt": 1,
                },
            )
        try:
            if material_binding is not None:
                raw = adapter.execute_native_package(  # type: ignore[attr-defined]
                    description,
                    {
                        "request_id": request["request_id"],
                        "request_sha256": canonical_sha256(request),
                        "input": deepcopy(request["input"]),
                        "catalog_snapshot_sha256": self.catalog.snapshot_sha256,
                        "source_lock": deepcopy(description["source_lock"]),
                        "candidate_only": True,
                    },
                )
            elif self.catalog is not None:
                if self.execution_environment is not None:
                    raw = adapter.execute(  # type: ignore[attr-defined]
                        description,
                        request["input"],
                        execution_identity=execution_identity,
                        operation_receipt=operation_receipt,
                    )
                else:
                    raw = adapter.execute(description, request["input"])  # type: ignore[attr-defined]
            else:
                if self.execution_environment is not None:
                    raw = adapter.execute(  # type: ignore[attr-defined]
                        request["input"],
                        execution_identity=execution_identity,
                        operation_receipt=operation_receipt,
                    )
                else:
                    raw = adapter.execute(request["input"])  # type: ignore[attr-defined]
        except CapabilityGatewayError:
            raise
        except Exception as error:
            raise CapabilityGatewayError(
                "capability_execution_failure",
                f"capability failed: {type(error).__name__}",
                retryable=True,
                next_route="retry-or-switch-capability",
            ) from error
        if (
            self.remaining_budget_seconds is not None
            and self._remaining_seconds() <= 0
        ):
            raise CapabilityGatewayError(
                "capability_deadline_exhausted",
                "capability execution consumed the answer deadline",
                retryable=False,
                next_route="return-bounded-conclusion-or-gap",
            )
        if not isinstance(raw, Mapping) or "content" not in raw:
            raise CapabilityGatewayError(
                "invalid_capability_result",
                "capability result is not source-bound",
                retryable=False,
                next_route="repair-capability-adapter",
            )
        content = deepcopy(raw["content"])
        output_acceptance = None
        if self.execution_environment is not None:
            output_acceptance = self.execution_environment.validate_result(content)
        result = {
            "contract": "lca_research_kernel.sourced_capability_result/v1",
            "request_id": request["request_id"],
            "capability_id": capability_id,
            "status": "sourced_result_not_fact",
            "effect": request["effect"],
            "authority": (
                self._public_description(description)
                if self.catalog is not None
                else deepcopy(description)
            ),
            "source_identity": raw.get("source_identity"),
            "source_version": raw.get("source_version"),
            "content": content,
            "content_sha256": canonical_sha256(content),
            "executed_external_writes": 0,
        }
        if material_binding is not None:
            native_receipt = self._validate_native_operation_receipt(
                raw.get("operation_receipt"),
                request=request,
                description=description,
                content=content,
                material_binding=material_binding,
            )
            result.update(
                {
                    "contract": (
                        "lca_research_kernel.sourced_lca_package_capability_result/v1"
                    ),
                    "candidate_only": True,
                    "domain_assessment": "pending_lca_domain_kernel",
                    "project_record_write": "not_performed",
                    **material_binding,
                    "source_identity": native_receipt["source_identity"],
                    "source_version": native_receipt["source_version"],
                    "operation_receipt": native_receipt,
                }
            )
            evidence = {
                "contract": "lca_research_kernel.native_lca_execution_evidence/v1",
                "request_id": request["request_id"],
                "request_sha256": canonical_sha256(request),
                "operation_intent_sha256": canonical_sha256(
                    request["operation_intent"]
                ),
                "action_authorization_sha256": canonical_sha256(
                    request["authorization"]
                ),
                "input_sha256": canonical_sha256(request["input"]),
                "input_artifact_sha256": material_binding[
                    "input_artifact_sha256"
                ],
                "archive_manifest_sha256": material_binding[
                    "archive_manifest_sha256"
                ],
                "member_hashes": deepcopy(material_binding["member_hashes"]),
                "capability_descriptor_sha256": canonical_sha256(description),
                "source_lock_sha256": canonical_sha256(
                    description["source_lock"]
                ),
                "catalog_snapshot_sha256": self.catalog.snapshot_sha256,
                "capability_id": capability_id,
                "adapter_qualification_sha256": canonical_sha256(
                    native_qualification
                ),
                "operation_receipt_sha256": native_receipt["receipt_sha256"],
                "result_sha256": canonical_sha256(result),
                "candidate_only": True,
            }
            if self.native_evidence_trace is None or not callable(
                self.native_evidence_clock
            ):
                raise ValueError("native capability evidence trace is absent")
            evidence_sha256 = canonical_sha256(evidence)
            envelope = {
                "contract": (
                    "lca_research_kernel.run_native_capability_evidence/v1"
                ),
                "request_id": request["request_id"],
                "operation_receipt_sha256": native_receipt["receipt_sha256"],
                "evidence_sha256": evidence_sha256,
                "evidence": evidence,
            }
            envelope["gateway_authentication"] = self._sign_native_evidence(
                envelope
            )
            self.native_evidence_trace.append(
                NATIVE_EVIDENCE_EVENT_TYPE,
                envelope,
                idempotency_key=f"native-capability-evidence:{evidence_sha256}",
                occurred_at=self.native_evidence_clock(),
            )
        if self.execution_environment is not None:
            result["execution_policy_sha256"] = self.execution_environment.policy_sha256
            result["operation_receipt_sha256"] = operation_receipt["receipt_sha256"]
            result["runtime_output_acceptance"] = output_acceptance
            if network_authorization is not None:
                result["network_authorization"] = deepcopy(
                    dict(network_authorization)
                )
        return result

    @staticmethod
    def _validate_native_material_adapter(
        adapter: object, description: Mapping[str, object]
    ) -> dict:
        if not hasattr(adapter, "native_package_qualification") or not hasattr(
            adapter, "execute_native_package"
        ):
            raise ValueError("native material adapter is not qualified")
        qualification = adapter.native_package_qualification()  # type: ignore[attr-defined]
        source_lock = description.get("source_lock")
        if (
            not isinstance(qualification, Mapping)
            or qualification.get("contract")
            != "lca_research_kernel.native_lca_adapter_qualification/v1"
            or qualification.get("capability_id") != description.get("capability_id")
            or qualification.get("adapter_id") != description.get("adapter_id")
            or not isinstance(source_lock, Mapping)
            or qualification.get("source_identity") != source_lock.get("identity")
            or qualification.get("source_version") != source_lock.get("version")
        ):
            raise ValueError("native material adapter qualification drifted")
        return deepcopy(dict(qualification))

    def with_native_evidence_trace(
        self, trace: object, *, clock: Callable[[], str]
    ) -> "CapabilityGateway":
        if not hasattr(trace, "append") or not hasattr(
            trace, "lookup_native_capability_evidence"
        ):
            raise ValueError("native capability evidence trace is unsupported")
        if not callable(clock):
            raise ValueError("native capability evidence clock is absent")
        clone = object.__new__(CapabilityGateway)
        clone.catalog = self.catalog
        clone.adapters_by_id = self.adapters_by_id
        clone.capabilities_by_id = self.capabilities_by_id
        clone.execution_environment = self.execution_environment
        clone.remaining_budget_seconds = self.remaining_budget_seconds
        clone.answer_timing = self.answer_timing
        clone.native_evidence_trace = trace
        clone.native_evidence_clock = clock
        clone._native_evidence_signer = self._native_evidence_signer
        clone._native_evidence_verifier = self._native_evidence_verifier
        return clone

    def _native_evidence_authentication_message(
        self, envelope: Mapping[str, object]
    ) -> bytes:
        trace_run_id = getattr(self.native_evidence_trace, "run_id", None)
        evidence = envelope.get("evidence")
        if (
            not isinstance(trace_run_id, str)
            or not trace_run_id
            or not isinstance(envelope.get("request_id"), str)
            or not isinstance(envelope.get("operation_receipt_sha256"), str)
            or not isinstance(envelope.get("evidence_sha256"), str)
            or not isinstance(evidence, Mapping)
        ):
            raise ValueError("native evidence authentication basis is invalid")
        return _canonical_json_bytes(
            {
                "contract": (
                    "lca_research_kernel.native_evidence_authentication_basis/v1"
                ),
                "run_id": trace_run_id,
                "event_type": NATIVE_EVIDENCE_EVENT_TYPE,
                "request_id": envelope["request_id"],
                "operation_receipt_sha256": envelope[
                    "operation_receipt_sha256"
                ],
                "evidence_sha256": envelope["evidence_sha256"],
                "evidence": deepcopy(dict(evidence)),
            }
        )

    def _sign_native_evidence(self, envelope: Mapping[str, object]) -> dict:
        if not callable(self._native_evidence_signer):
            raise ValueError("native evidence authenticator is absent")
        tag = self._native_evidence_signer(
            self._native_evidence_authentication_message(envelope)
        )
        if (
            not isinstance(tag, str)
            or len(tag) != 64
            or any(character not in "0123456789abcdef" for character in tag)
        ):
            raise ValueError("native evidence authenticator returned an invalid tag")
        return {
            "contract": NATIVE_EVIDENCE_AUTHENTICATION_CONTRACT,
            "algorithm": "hmac-sha256",
            "tag": tag,
        }

    def _verify_native_evidence_authentication(
        self, envelope: Mapping[str, object]
    ) -> bool:
        authentication = envelope.get("gateway_authentication")
        if (
            not callable(self._native_evidence_verifier)
            or not isinstance(authentication, Mapping)
            or set(authentication) != {"contract", "algorithm", "tag"}
            or authentication.get("contract")
            != NATIVE_EVIDENCE_AUTHENTICATION_CONTRACT
            or authentication.get("algorithm") != "hmac-sha256"
            or not isinstance(authentication.get("tag"), str)
        ):
            return False
        try:
            return self._native_evidence_verifier(
                self._native_evidence_authentication_message(envelope),
                authentication["tag"],
            ) is True
        except (TypeError, ValueError):
            return False

    def verify_native_result(
        self,
        result: Mapping[str, object],
        *,
        operation_intent: Mapping[str, object],
        authorized_request: Mapping[str, object],
    ) -> dict:
        """Verify a native candidate against append-only gateway evidence."""

        from .assurance import AssuranceBoundary

        receipt = result.get("operation_receipt")
        receipt_sha256 = (
            receipt.get("receipt_sha256") if isinstance(receipt, Mapping) else None
        )
        request_id = result.get("request_id")
        evidence = None
        if (
            self.native_evidence_trace is not None
            and isinstance(request_id, str)
            and isinstance(receipt_sha256, str)
        ):
            envelope = self.native_evidence_trace.lookup_native_capability_evidence(
                request_id=request_id,
                operation_receipt_sha256=receipt_sha256,
            )
            if (
                isinstance(envelope, Mapping)
                and self._verify_native_evidence_authentication(envelope)
            ):
                candidate_evidence = envelope.get("evidence")
                if isinstance(candidate_evidence, Mapping):
                    evidence = deepcopy(dict(candidate_evidence))
        errors = self._native_result_evidence_errors(
            result,
            operation_intent=operation_intent,
            authorized_request=authorized_request,
            registered_evidence=evidence,
            assurance=AssuranceBoundary(),
        )
        return {
            "contract": "lca_research_kernel.sourced_capability_acceptance/v1",
            "decision": "accepted" if not errors else "rejected",
            "result_sha256": canonical_sha256(result),
            "evaluator": "assurance_boundary_via_capability_evidence_port",
            "errors": errors,
            "may_promote_result_to_fact": False,
        }

    def _native_result_evidence_errors(
        self,
        result: Mapping[str, object],
        *,
        operation_intent: Mapping[str, object],
        authorized_request: Mapping[str, object],
        registered_evidence: Mapping[str, object] | None,
        assurance: object,
    ) -> list[str]:
        receipt = result.get("operation_receipt")
        authority = result.get("authority")
        unsigned = deepcopy(dict(receipt)) if isinstance(receipt, Mapping) else {}
        receipt_sha256 = unsigned.pop("receipt_sha256", None)
        payload_ref = operation_intent.get("payload_ref") if isinstance(
            operation_intent, Mapping
        ) else None
        request_input = authorized_request.get("input") if isinstance(
            authorized_request, Mapping
        ) else None
        trusted_descriptor = None
        if self.catalog is not None and isinstance(result.get("capability_id"), str):
            try:
                trusted_descriptor = self.catalog.get(result["capability_id"])
            except (KeyError, ValueError):
                trusted_descriptor = None
        trusted_source_lock = trusted_descriptor.get("source_lock") if isinstance(
            trusted_descriptor, Mapping
        ) else None
        if (
            result.get("contract")
            != "lca_research_kernel.sourced_lca_package_capability_result/v1"
            or result.get("status") != "sourced_result_not_fact"
            or result.get("executed_external_writes") != 0
            or not isinstance(operation_intent, Mapping)
            or not isinstance(authorized_request, Mapping)
            or not isinstance(request_input, Mapping)
            or not isinstance(trusted_descriptor, Mapping)
            or not isinstance(trusted_source_lock, Mapping)
            or not isinstance(registered_evidence, Mapping)
        ):
            return ["native_lca_gateway_verification_required"]
        expected_authorization = assurance.authorize_action(operation_intent)
        expected_qualification = {
            "contract": "lca_research_kernel.native_lca_adapter_qualification/v1",
            "capability_id": trusted_descriptor.get("capability_id"),
            "adapter_id": trusted_descriptor.get("adapter_id"),
            "source_identity": trusted_source_lock.get("identity"),
            "source_version": trusted_source_lock.get("version"),
        }
        invalid = (
            result.get("candidate_only") is not True
            or result.get("project_record_write") != "not_performed"
            or result.get("domain_assessment") != "pending_lca_domain_kernel"
            or not isinstance(receipt, Mapping)
            or receipt.get("contract")
            != "lca_research_kernel.native_lca_operation_receipt/v1"
            or receipt_sha256 != canonical_sha256(unsigned)
            or authorized_request.get("contract") != REQUEST_CONTRACT
            or authorized_request.get("request_id")
            != operation_intent.get("operation_id")
            or authorized_request.get("capability_id")
            != operation_intent.get("target")
            or authorized_request.get("effect") != operation_intent.get("effect")
            or authorized_request.get("input") != operation_intent.get("payload")
            or authorized_request.get("operation_intent") != operation_intent
            or authorized_request.get("authorization") != expected_authorization
            or expected_authorization.get("authorized") is not True
            or not isinstance(payload_ref, Mapping)
            or payload_ref.get("sha256")
            != canonical_sha256(operation_intent.get("payload"))
            or authorized_request.get("catalog_snapshot_sha256")
            != self.catalog.snapshot_sha256
            or authorized_request.get("source_lock_id")
            != trusted_descriptor.get("source_lock_id")
            or result.get("request_id") != authorized_request.get("request_id")
            or result.get("capability_id") != trusted_descriptor.get("capability_id")
            or result.get("effect") != trusted_descriptor.get("effect")
            or receipt.get("request_id") != authorized_request.get("request_id")
            or receipt.get("request_sha256") != canonical_sha256(authorized_request)
            or registered_evidence.get("contract")
            != "lca_research_kernel.native_lca_execution_evidence/v1"
            or registered_evidence.get("request_id")
            != authorized_request.get("request_id")
            or registered_evidence.get("request_sha256")
            != canonical_sha256(authorized_request)
            or registered_evidence.get("operation_intent_sha256")
            != canonical_sha256(operation_intent)
            or registered_evidence.get("action_authorization_sha256")
            != canonical_sha256(authorized_request.get("authorization"))
            or registered_evidence.get("input_sha256")
            != canonical_sha256(request_input)
            or registered_evidence.get("input_artifact_sha256")
            != request_input.get("input_artifact_sha256")
            or registered_evidence.get("archive_manifest_sha256")
            != request_input.get("archive_manifest_sha256")
            or registered_evidence.get("member_hashes")
            != request_input.get("member_hashes")
            or registered_evidence.get("capability_descriptor_sha256")
            != canonical_sha256(trusted_descriptor)
            or registered_evidence.get("source_lock_sha256")
            != canonical_sha256(trusted_source_lock)
            or registered_evidence.get("catalog_snapshot_sha256")
            != self.catalog.snapshot_sha256
            or registered_evidence.get("capability_id")
            != trusted_descriptor.get("capability_id")
            or registered_evidence.get("adapter_qualification_sha256")
            != canonical_sha256(expected_qualification)
            or registered_evidence.get("operation_receipt_sha256")
            != receipt_sha256
            or registered_evidence.get("result_sha256") != canonical_sha256(result)
            or registered_evidence.get("candidate_only") is not True
            or receipt.get("capability_id")
            != trusted_descriptor.get("capability_id")
            or not isinstance(authority, Mapping)
            or any(authority.get(key) != value for key, value in trusted_descriptor.items())
            or authority.get("authority") != trusted_descriptor.get("authority_tier")
            or authority.get("access")
            != (
                "read_only"
                if trusted_descriptor.get("effect")
                in {"external_read", "local_read"}
                else "write"
            )
            or authority.get("catalog_snapshot_sha256")
            != self.catalog.snapshot_sha256
            or receipt.get("adapter_id") != trusted_descriptor.get("adapter_id")
            or receipt.get("input_sha256") != canonical_sha256(request_input)
            or receipt.get("input_artifact_sha256")
            != request_input.get("input_artifact_sha256")
            or receipt.get("archive_manifest_sha256")
            != request_input.get("archive_manifest_sha256")
            or receipt.get("consumed_member_hashes")
            != request_input.get("member_hashes")
            or result.get("input_artifact_sha256")
            != request_input.get("input_artifact_sha256")
            or result.get("archive_manifest_sha256")
            != request_input.get("archive_manifest_sha256")
            or result.get("member_hashes") != request_input.get("member_hashes")
            or receipt.get("output_sha256") != result.get("content_sha256")
            or result.get("content_sha256") != canonical_sha256(result.get("content"))
            or receipt.get("source_identity") != trusted_source_lock.get("identity")
            or receipt.get("source_version") != trusted_source_lock.get("version")
            or result.get("source_identity") != receipt.get("source_identity")
            or result.get("source_version") != receipt.get("source_version")
        )
        return ["native_lca_candidate_receipt_invalid"] if invalid else []

    @staticmethod
    def _validate_native_operation_receipt(
        value: object,
        *,
        request: Mapping[str, object],
        description: Mapping[str, object],
        content: object,
        material_binding: Mapping[str, object],
    ) -> dict:
        if not isinstance(value, Mapping):
            raise ValueError("native operation receipt is absent")
        receipt = deepcopy(dict(value))
        claimed_sha256 = receipt.pop("receipt_sha256", None)
        source_lock = description.get("source_lock")
        expected = {
            "contract": "lca_research_kernel.native_lca_operation_receipt/v1",
            "request_id": request.get("request_id"),
            "request_sha256": canonical_sha256(request),
            "capability_id": description.get("capability_id"),
            "adapter_id": description.get("adapter_id"),
            "source_identity": (
                source_lock.get("identity") if isinstance(source_lock, Mapping) else None
            ),
            "source_version": (
                source_lock.get("version") if isinstance(source_lock, Mapping) else None
            ),
            "input_sha256": canonical_sha256(request.get("input")),
            "input_artifact_sha256": material_binding["input_artifact_sha256"],
            "archive_manifest_sha256": material_binding["archive_manifest_sha256"],
            "consumed_member_hashes": material_binding["member_hashes"],
            "output_sha256": canonical_sha256(content),
            "candidate_only": True,
        }
        if receipt != expected or claimed_sha256 != canonical_sha256(receipt):
            raise ValueError("native operation receipt binding is invalid")
        return {**receipt, "receipt_sha256": claimed_sha256}

    @staticmethod
    def _native_material_binding(payload: Mapping[str, object]) -> dict | None:
        """Validate an all-or-nothing package identity before adapter execution."""

        keys = ("input_artifact_sha256", "archive_manifest_sha256", "member_hashes")
        values = tuple(payload.get(key) for key in keys)
        if not any(value is not None for value in values):
            return None
        input_artifact_sha256, archive_manifest_sha256, member_hashes = values
        for label, value in (
            ("input artifact", input_artifact_sha256),
            ("archive manifest", archive_manifest_sha256),
        ):
            if not isinstance(value, str) or len(value) != 64 or any(
                char not in "0123456789abcdef" for char in value
            ):
                raise ValueError(f"native material {label} hash is invalid")
        if (
            not isinstance(member_hashes, list)
            or not member_hashes
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                for value in member_hashes
            )
        ):
            raise ValueError("native material member hashes are invalid")
        return {
            "input_artifact_sha256": input_artifact_sha256,
            "archive_manifest_sha256": archive_manifest_sha256,
            "member_hashes": deepcopy(member_hashes),
        }

    def with_execution_environment(self, environment: object) -> "CapabilityGateway":
        clone = object.__new__(CapabilityGateway)
        clone.catalog = self.catalog
        clone.adapters_by_id = self.adapters_by_id
        clone.capabilities_by_id = self.capabilities_by_id
        clone.execution_environment = environment
        clone.remaining_budget_seconds = self.remaining_budget_seconds
        clone.answer_timing = self.answer_timing
        clone.native_evidence_trace = self.native_evidence_trace
        clone.native_evidence_clock = self.native_evidence_clock
        clone._native_evidence_signer = self._native_evidence_signer
        clone._native_evidence_verifier = self._native_evidence_verifier
        return clone

    def with_remaining_budget(
        self, remaining_budget_seconds: Callable[[], float]
    ) -> "CapabilityGateway":
        if not callable(remaining_budget_seconds):
            raise ValueError("capability remaining budget port must be callable")
        clone = object.__new__(CapabilityGateway)
        clone.catalog = self.catalog
        clone.adapters_by_id = self.adapters_by_id
        clone.capabilities_by_id = self.capabilities_by_id
        clone.execution_environment = self.execution_environment
        prior = self.remaining_budget_seconds
        clone.remaining_budget_seconds = (
            remaining_budget_seconds
            if prior is None
            else lambda: min(
                float(prior()), float(remaining_budget_seconds())
            )
        )
        clone.answer_timing = self.answer_timing
        clone.native_evidence_trace = self.native_evidence_trace
        clone.native_evidence_clock = self.native_evidence_clock
        clone._native_evidence_signer = self._native_evidence_signer
        clone._native_evidence_verifier = self._native_evidence_verifier
        return clone

    def with_answer_timing(self, answer_timing: object) -> "CapabilityGateway":
        clone = self.with_remaining_budget(answer_timing.remaining_seconds)
        clone.answer_timing = answer_timing
        return clone

    @property
    def handles_answer_timing(self) -> bool:
        return self.answer_timing is not None

    def _remaining_seconds(self) -> float:
        if self.remaining_budget_seconds is None:
            raise ValueError("capability remaining budget port is absent")
        value = self.remaining_budget_seconds()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value < 0
        ):
            raise ValueError("capability remaining budget is invalid")
        return float(value)

    def describe_authority(self, capability_id: str) -> dict:
        capability = self.capabilities_by_id.get(capability_id)
        if capability is None:
            raise ValueError(f"unknown capability: {capability_id}")
        if self.catalog is not None:
            return self._public_description(capability)
        return deepcopy(capability.describe())  # type: ignore[attr-defined]

    def describe_availability(self, capability_id: str) -> dict:
        """Expose catalog state separately from actual adapter availability."""

        description = self.describe_authority(capability_id)
        if self.catalog is None:
            return {
                "capability_id": capability_id,
                "availability": "available_local",
                "runtime_available": True,
            }
        adapter_id = str(description.get("adapter_id"))
        return {
            "capability_id": capability_id,
            "availability": description.get("availability"),
            "disposition": description.get("disposition"),
            "effect": description.get("effect"),
            "adapter_id": adapter_id,
            "runtime_available": self._runtime_available(description),
            "recovery_route": description.get("recovery_route"),
            "source_lock": deepcopy(description.get("source_lock")),
        }

    @staticmethod
    def _adapter_supports(
        adapter: object, description: Mapping[str, object]
    ) -> bool:
        supports = getattr(adapter, "supports_capability", None)
        if supports is None:
            return True
        if not callable(supports):
            return False
        return supports(deepcopy(dict(description))) is True

    def _runtime_available(self, description: Mapping[str, object]) -> bool:
        adapter = self.adapters_by_id.get(str(description.get("adapter_id")))
        if adapter is None or not self._adapter_supports(adapter, description):
            return False
        available = getattr(adapter, "runtime_available_for_capability", None)
        if available is None:
            return True
        if not callable(available):
            return False
        return available(deepcopy(dict(description))) is True

    def _public_description(self, capability: Mapping[str, object]) -> dict:
        description = deepcopy(dict(capability))
        description["authority"] = description.get("authority_tier")
        description["access"] = (
            "read_only"
            if description.get("effect") in {"external_read", "local_read"}
            else "write"
        )
        description["public_credential_free"] = (
            description.get("credential_policy") == "public_credential_free"
        )
        if self.catalog is not None:
            description["catalog_snapshot_sha256"] = self.catalog.snapshot_sha256
        adapter = self.adapters_by_id.get(str(description.get("adapter_id")))
        if adapter is not None and hasattr(adapter, "context_descriptor"):
            context_descriptor = adapter.context_descriptor()  # type: ignore[attr-defined]
            if not isinstance(context_descriptor, Mapping):
                raise ValueError("capability runtime context is invalid")
            description["runtime_context"] = {
                "official_source_locator": deepcopy(
                    dict(context_descriptor)
                )
            }
        return description

    def descriptor(self, capability_id: str) -> dict:
        return self.describe_authority(capability_id)

    def execution_descriptor(self, capability_id: str) -> dict:
        description = self.describe_authority(capability_id)
        source = description.get("source_lock")
        if not isinstance(source, Mapping):
            source = {
                "identity": capability_id,
                "version": str(
                    description.get("source_version")
                    or "local-deterministic/v1"
                ),
                "integrity": "sha256:" + canonical_sha256(description),
            }
        entrypoint = description.get("entrypoint", capability_id)
        return {
            **description,
            "adapter_id": description.get("adapter_id", capability_id),
            "transport": description.get("transport", "in_process"),
            "entrypoint": deepcopy(entrypoint),
            "input_contract": description.get(
                "input_contract", f"{capability_id}.input/v1"
            ),
            "source_lock": deepcopy(dict(source)),
            "tool_contract": {
                "name": deepcopy(entrypoint),
                "description": (
                    "Execute the exact source-bound capability under the project "
                    "execution policy."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": True,
                },
            },
        }

    def normalize_failure(self, error: BaseException) -> dict:
        if isinstance(error, CapabilityGatewayError):
            return {
                "contract": "lca_research_kernel.capability_failure/v1",
                "code": error.code,
                "message": str(error),
                "retryable": error.retryable,
                "next_route": error.next_route,
                "may_promote_result_to_fact": False,
            }
        return {
            "contract": "lca_research_kernel.capability_failure/v1",
            "code": "unknown_capability_failure",
            "message": type(error).__name__,
            "retryable": False,
            "next_route": "inspect-capability-harness",
            "may_promote_result_to_fact": False,
        }

    def _validate_request(self, value: Mapping[str, object]) -> dict:
        if not isinstance(value, Mapping) or value.get("contract") != REQUEST_CONTRACT:
            raise ValueError("capability request contract is unsupported")
        request = deepcopy(dict(value))
        authorization = request.get("authorization")
        if not isinstance(authorization, Mapping) or authorization.get(
            "authorized"
        ) is not True:
            raise ValueError("capability request is not authorized")
        intent = request.get("operation_intent")
        if not isinstance(intent, Mapping):
            raise ValueError("capability operation intent is absent")
        if authorization.get("intent_sha256") != canonical_sha256(intent):
            raise ValueError("capability authorization payload drifted")
        for request_key, intent_key in (
            ("request_id", "operation_id"),
            ("capability_id", "target"),
            ("effect", "effect"),
        ):
            if request.get(request_key) != intent.get(intent_key):
                raise ValueError("capability request differs from its operation intent")
        if request.get("input") != intent.get("payload"):
            raise ValueError("capability request payload drifted after authorization")
        return request
