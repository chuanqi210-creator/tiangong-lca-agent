import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "lca_research_kernel"
    / "contracts"
    / "d18-runtime-architecture-semantics.json"
)
EXPECTED_MODULES = {
    "product_interface",
    "research_orchestrator",
    "lca_domain_kernel",
    "model_gateway",
    "capability_gateway",
    "project_record",
    "assurance_boundary",
}


def reference(label):
    return {
        "path": f"evidence/{label}.json",
        "sha256": hashlib.sha256(label.encode("utf-8")).hexdigest(),
    }


class ArchitectureSemanticsTests(unittest.TestCase):
    def setUp(self):
        from lca_research_kernel.architecture_semantics import ArchitectureSemantics

        self.semantics = ArchitectureSemantics.load(ROOT, CONTRACT_PATH)
        self.goal_ref = {
            "goal_id": "d18-production-runtime-core",
            "objective_sha256": "1" * 64,
        }
        self.project_state_ref = {
            "project_id": "runtime-probe",
            "projection_sha256": "2" * 64,
        }
        self.problem = {
            "problem_id": "missing-source-bound-context",
            "state": "research_goal_accepted",
            "required_responsibility": "research_orchestrator",
            "evidence_refs": [reference("project-view")],
        }
        self.expected_change = {
            "from_state": "research_goal_accepted",
            "to_state": "decision_context_assembled",
            "observable_delta": [
                "one source-bound decision context is registered as a candidate"
            ],
        }
        self.routes = [
            {
                "route_id": "assemble-decision-context",
                "responsibility": "research_orchestrator",
                "from_state": "research_goal_accepted",
                "to_state": "decision_context_assembled",
                "capability_ids": [],
                "architecture_principles": [
                    "project_record_is_the_only_accepted_truth",
                    "minimum_source_bound_context",
                ],
                "acceptance_conditions": [
                    "all selected context items retain source and trust labels"
                ],
                "permission_boundary": {
                    "allowed_effects": ["local_read"],
                    "human_owned_effects": [
                        "credentials",
                        "payment",
                        "publication",
                        "external_write",
                        "irreversible",
                    ],
                },
                "claim_ceiling": "candidate_only",
                "auditable_reason": (
                    "The accepted project view lacks a source-bound decision context; "
                    "assembling the minimum relevant facts creates the stated observable "
                    "delta without changing authority or permissions."
                ),
            },
            {
                "route_id": "query-available-tool",
                "responsibility": "capability_gateway",
                "from_state": "research_goal_accepted",
                "to_state": "tool_result_available",
                "capability_ids": ["local.inspect"],
                "architecture_principles": ["tools_execute_only_authorized_requests"],
                "acceptance_conditions": ["one sourced tool result exists"],
                "permission_boundary": {
                    "allowed_effects": ["local_read"],
                    "human_owned_effects": ["external_write"],
                },
                "claim_ceiling": "sourced_result_not_fact",
                "auditable_reason": "A tool is available but does not close the current context gap.",
            },
        ]

    def frame(self):
        return self.semantics.select_by_state_change(
            goal_ref=self.goal_ref,
            project_state_ref=self.project_state_ref,
            current_problem=self.problem,
            expected_state_change=self.expected_change,
            candidate_routes=self.routes,
        )

    def test_contract_compiles_exactly_the_seven_d18_modules(self):
        self.assertEqual(set(self.semantics.module_ids), EXPECTED_MODULES)
        self.assertEqual(len(self.semantics.module_ids), 7)
        for descriptor in self.semantics.modules:
            for key in (
                "purpose",
                "triggers",
                "inputs",
                "artifacts",
                "forbidden",
                "dependencies",
                "failure_boundaries",
            ):
                self.assertTrue(descriptor[key], (descriptor["id"], key))
        self.assertEqual(
            self.semantics.contract["ownership"]["architecture_semantics"],
            "research_orchestrator",
        )
        self.assertNotIn("architecture_understanding", self.semantics.module_ids)

    def test_selects_by_state_change_and_responsibility_not_tool_or_case_words(self):
        frame = self.frame()
        self.assertEqual(
            frame["contract"],
            "lca_research_kernel.architecture_decision_frame/v1",
        )
        self.assertEqual(frame["selected_modules"], ["research_orchestrator"])
        self.assertEqual(frame["selected_capabilities"], [])
        self.assertEqual(frame["next_route"], "assemble-decision-context")
        self.assertEqual(
            frame["selection_basis"], "state_transition_and_responsibility"
        )
        self.assertRegex(frame["frame_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(self.semantics.validate_frame(frame), frame)

    def test_keyword_changes_do_not_change_the_selected_responsibility(self):
        for wording in (
            "新能源汽车材料 context needs a bounded source set",
            "A novel alloy study needs a bounded source set",
            "请调用最强工具和 Council，但当前仍缺最小来源上下文",
        ):
            problem = copy.deepcopy(self.problem)
            problem["summary"] = wording
            frame = self.semantics.select_by_state_change(
                goal_ref=self.goal_ref,
                project_state_ref=self.project_state_ref,
                current_problem=problem,
                expected_state_change=self.expected_change,
                candidate_routes=self.routes,
            )
            self.assertEqual(frame["selected_modules"], ["research_orchestrator"])
            self.assertEqual(frame["next_route"], "assemble-decision-context")

    def test_no_state_change_wrong_owner_and_duplicate_wrapper_fail_closed(self):
        no_change = copy.deepcopy(self.expected_change)
        no_change["to_state"] = no_change["from_state"]
        with self.assertRaisesRegex(ValueError, "expected state change"):
            self.semantics.select_by_state_change(
                goal_ref=self.goal_ref,
                project_state_ref=self.project_state_ref,
                current_problem=self.problem,
                expected_state_change=no_change,
                candidate_routes=self.routes,
            )

        wrong_owner = copy.deepcopy(self.frame())
        wrong_owner["selected_modules"] = ["capability_gateway"]
        wrong_owner.pop("frame_sha256")
        wrong_owner["frame_sha256"] = self.semantics.frame_sha256(wrong_owner)
        with self.assertRaisesRegex(ValueError, "responsibility"):
            self.semantics.validate_frame(wrong_owner)

        duplicate = copy.deepcopy(self.frame())
        duplicate["selected_modules"].append("research_orchestrator")
        duplicate.pop("frame_sha256")
        duplicate["frame_sha256"] = self.semantics.frame_sha256(duplicate)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.semantics.validate_frame(duplicate)

    def test_hidden_reasoning_and_pure_keyword_basis_are_rejected(self):
        hidden = copy.deepcopy(self.frame())
        hidden["hidden_reasoning"] = "private internal monologue"
        hidden.pop("frame_sha256")
        hidden["frame_sha256"] = self.semantics.frame_sha256(hidden)
        with self.assertRaisesRegex(ValueError, "fields|hidden reasoning"):
            self.semantics.validate_frame(hidden)

        keyword = copy.deepcopy(self.frame())
        keyword["selection_basis"] = "keyword_match"
        keyword.pop("frame_sha256")
        keyword["frame_sha256"] = self.semantics.frame_sha256(keyword)
        with self.assertRaisesRegex(ValueError, "selection basis"):
            self.semantics.validate_frame(keyword)

    def test_architecture_source_and_contract_drift_fail_closed(self):
        from lca_research_kernel.architecture_semantics import ArchitectureSemantics

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "contracts").mkdir()
            architecture = ROOT / "docs/lca-agent-top-level-architecture.json"
            (root / "docs/architecture.json").write_bytes(architecture.read_bytes())
            contract = copy.deepcopy(self.semantics.contract)
            contract["architecture_ref"]["path"] = "docs/architecture.json"
            contract_path = root / "contracts/semantics.json"
            contract_path.write_text(
                json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            payload = json.loads(
                (root / "docs/architecture.json").read_text(encoding="utf-8")
            )
            payload["modules"][0]["owns"].append("drift")
            (root / "docs/architecture.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "architecture source drifted"):
                ArchitectureSemantics.load(root, contract_path)


if __name__ == "__main__":
    unittest.main()
