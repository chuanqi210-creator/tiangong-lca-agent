import ast
import hashlib
import json
import unittest
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "lca_research_kernel/capability_gateway.py"


def operation_intent(request_id, capability_id, effect, payload, tags=()):
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "contract": "lca_research_kernel.operation_intent/v1",
        "operation_id": request_id,
        "effect": effect,
        "responsibility_tags": list(tags),
        "target": capability_id,
        "scope": "research:capability-test",
        "payload": payload,
        "payload_ref": {
            "path": f"outputs/capability/{request_id}.json",
            "sha256": digest,
            "version": "v1",
        },
        "verification": "verify sourced result envelope",
        "compensation": "discard unaccepted local artifact",
        "resume_route": "resume-after-capability",
    }


class CapabilityGatewayTests(unittest.TestCase):
    def setUp(self):
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.capability_gateway import (
            CapabilityGateway,
            LocalDeterministicCapability,
            TianGongReadOnlyCapability,
        )

        self.assurance = AssuranceBoundary()
        self.tiangong_calls = []

        def tiangong_reader(payload):
            self.tiangong_calls.append(payload)
            return {
                "identity": "process-1",
                "version": "01.01.000",
                "source": "TianGong Open Data",
                "payload": {"name": "source-bound process"},
            }

        self.gateway = CapabilityGateway(
            {
                "local.echo": LocalDeterministicCapability(
                    "local.echo", lambda payload: {"echo": payload}
                ),
                "tiangong.process.read": TianGongReadOnlyCapability(
                    "tiangong.process.read", tiangong_reader
                ),
            }
        )

    def request(self, capability_id, effect, payload, tags=()):
        intent = operation_intent(
            "capability-request-1", capability_id, effect, payload, tags
        )
        return {
            "contract": "lca_research_kernel.capability_request/v1",
            "request_id": "capability-request-1",
            "capability_id": capability_id,
            "effect": effect,
            "input": payload,
            "operation_intent": intent,
            "authorization": self.assurance.authorize_action(intent),
        }

    def test_discovery_and_authority_are_explicit(self):
        capabilities = self.gateway.list_capabilities({"research_id": "fixture"})
        self.assertEqual(
            [row["capability_id"] for row in capabilities],
            ["local.echo", "tiangong.process.read"],
        )
        authority = self.gateway.describe_authority("tiangong.process.read")
        self.assertEqual(authority["authority"], "TianGong")
        self.assertEqual(authority["access"], "read_only")
        self.assertEqual(authority["result_status"], "sourced_result_not_fact")

    def test_local_deterministic_execution_returns_sourced_not_formal_result(self):
        result = self.gateway.execute(
            self.request("local.echo", "local_read", {"value": 3})
        )
        self.assertEqual(result["status"], "sourced_result_not_fact")
        self.assertEqual(result["content"], {"echo": {"value": 3}})
        self.assertEqual(result["effect"], "local_read")
        self.assertRegex(result["content_sha256"], r"^[0-9a-f]{64}$")

    def test_tiangong_read_preserves_official_identity_and_version(self):
        result = self.gateway.execute(
            self.request(
                "tiangong.process.read",
                "external_read",
                {"process_id": "process-1", "version": "01.01.000"},
                ["public_credential_free"],
            )
        )
        self.assertEqual(result["authority"]["authority"], "TianGong")
        self.assertEqual(result["source_identity"], "process-1")
        self.assertEqual(result["source_version"], "01.01.000")
        self.assertEqual(result["content"], {"name": "source-bound process"})
        self.assertEqual(len(self.tiangong_calls), 1)

    def test_pre_attempt_guard_runs_immediately_before_adapter_and_can_veto_it(self):
        guarded = []

        def pre_attempt(capability_request, attempt):
            guarded.append(
                (
                    capability_request["request_id"],
                    attempt["route_id"],
                    attempt["attempt"],
                )
            )
            raise ValueError("material execution basis drifted")

        with self.assertRaisesRegex(ValueError, "material execution basis drifted"):
            self.gateway.execute(
                self.request(
                    "tiangong.process.read",
                    "external_read",
                    {"process_id": "process-1", "version": "01.01.000"},
                    ["public_credential_free"],
                ),
                pre_attempt=pre_attempt,
            )

        self.assertEqual(
            guarded,
            [("capability-request-1", "tiangong.process.read", 1)],
        )
        self.assertEqual(self.tiangong_calls, [])

    def test_public_credential_free_tiangong_access_is_explicit_not_assumed(self):
        from lca_research_kernel.capability_gateway import TianGongReadOnlyCapability

        default = TianGongReadOnlyCapability("default", lambda payload: {})
        public = TianGongReadOnlyCapability(
            "public", lambda payload: {}, public_credential_free=True
        )
        self.assertFalse(default.describe()["public_credential_free"])
        self.assertTrue(public.describe()["public_credential_free"])

    def test_tiangong_read_only_input_is_positive_and_closed(self):
        from lca_research_kernel.capability_gateway import TianGongReadOnlyCapability

        capability = TianGongReadOnlyCapability(
            "tiangong.process.read",
            lambda payload: {
                "identity": payload["process_id"],
                "version": payload["version"],
                "payload": {},
            },
            public_credential_free=True,
            input_schema={
                "type": "object",
                "properties": {
                    "process_id": {"type": "string"},
                    "version": {"type": "string"},
                },
                "required": ["process_id", "version"],
                "additionalProperties": False,
            },
        )

        description = capability.describe()
        self.assertEqual(
            description["input_contract"],
            "lca_research_kernel.tiangong_read_only_nonmaterial_input/v1",
        )
        self.assertFalse(description["material_input"])
        self.assertFalse(description["input_schema"]["additionalProperties"])

    def test_authorization_or_payload_drift_fails_before_adapter_execution(self):
        rejected = self.request(
            "tiangong.process.read",
            "external_read",
            {"process_id": "process-1"},
        )
        with self.assertRaisesRegex(ValueError, "not authorized"):
            self.gateway.execute(rejected)
        self.assertEqual(self.tiangong_calls, [])
        drifted = self.request(
            "tiangong.process.read",
            "external_read",
            {"process_id": "process-1"},
            ["public_credential_free"],
        )
        drifted["input"]["process_id"] = "changed"
        with self.assertRaisesRegex(ValueError, "payload drifted"):
            self.gateway.execute(drifted)
        self.assertEqual(self.tiangong_calls, [])

    def test_failures_are_normalized_without_claim_promotion(self):
        from lca_research_kernel.capability_gateway import CapabilityGatewayError

        error = CapabilityGatewayError(
            "timeout", "bounded timeout", retryable=True, next_route="retry-capability"
        )
        normalized = self.gateway.normalize_failure(error)
        self.assertEqual(normalized["code"], "timeout")
        self.assertTrue(normalized["retryable"])
        self.assertFalse(normalized["may_promote_result_to_fact"])

    def test_gateway_has_no_project_case_or_council_dependency(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        joined = "\n".join(imports).lower()
        for forbidden in ("project", "council", "uslci", "lifepo4", "nev"):
            self.assertNotIn(forbidden, joined)

    def test_execution_boundary_receipt_is_required_and_validated_when_bound(self):
        from lca_research_kernel.capability_gateway import (
            CapabilityGateway,
            LocalDeterministicCapability,
        )
        from lca_research_kernel.execution_environment import (
            ProjectExecutionEnvironment,
        )
        from tests.test_execution_environment import policy

        with tempfile.TemporaryDirectory() as temporary:
            environment = ProjectExecutionEnvironment.create(
                Path(temporary), "capability-test", policy()
            )
            capability = LocalDeterministicCapability(
                "local.echo", lambda payload: {"echo": payload}
            )
            gateway = CapabilityGateway(
                {"local.echo": capability},
                execution_environment=environment,
            )
            request = self.request("local.echo", "local_read", {"value": 3})
            with self.assertRaisesRegex(ValueError, "execution"):
                gateway.execute(request)
            receipt = environment.authorize_tool(
                {
                    **capability.describe(),
                    "adapter_id": "local.echo",
                    "transport": "in_process",
                    "entrypoint": "local.echo",
                    "input_contract": "local.echo/v1",
                    "source_lock": {
                        "identity": "local.echo",
                        "version": "local-deterministic/v1",
                        "integrity": "sha256:" + "a" * 64,
                    },
                    "tool_contract": {
                        "name": "local.echo",
                        "description": "Read one bounded local value.",
                        "input_schema": {
                            "type": "object",
                            "required": ["value"],
                            "properties": {"value": {"type": "integer"}},
                            "additionalProperties": False,
                        },
                    },
                },
                request["input"],
                operation_id=request["request_id"],
            )
            request["execution_identity"] = environment.identity
            request["operation_receipt"] = receipt
            result = gateway.execute(request)
            self.assertEqual(result["content"], {"echo": {"value": 3}})
            self.assertEqual(
                result["execution_policy_sha256"],
                environment.identity["policy_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
