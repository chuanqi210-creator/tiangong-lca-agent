import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs/lca-agent-d18-core-contract.json"
MODULE_IDS = {
    "product_interface",
    "research_orchestrator",
    "lca_domain_kernel",
    "model_gateway",
    "capability_gateway",
    "project_record",
    "assurance_boundary",
}


class D18ArchitectureContractTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(CONTRACT_PATH.is_file(), "D-18 core contract is missing")
        self.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def validate(self, value=None):
        from lca_research_kernel.d18_architecture import validate_d18_core_contract

        if value is None:
            return validate_d18_core_contract(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            for name in (
                "lca-agent-top-level-architecture.json",
                "lca-agent-d18-existing-system-mapping.json",
            ):
                (root / "docs" / name).write_bytes((ROOT / "docs" / name).read_bytes())
            for module in value.get("modules", []):
                implementation = module.get("implementation", {})
                if implementation.get("status") != "implemented_focused_verified":
                    continue
                for key in ("module_path", "verification_test"):
                    relative = Path(implementation[key])
                    (root / relative).parent.mkdir(parents=True, exist_ok=True)
                    (root / relative).write_bytes((ROOT / relative).read_bytes())
            (root / "docs/lca-agent-d18-core-contract.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return validate_d18_core_contract(root)

    def test_checker_runs_and_accepts_the_repository_contract(self):
        result = subprocess.run(
            ["python3", "-m", "lca_research_kernel.d18_architecture", "--root", "."],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload, {"error_count": 0, "status": "passed"})
        self.assertEqual(self.validate(), [])

    def test_exact_seven_modules_ownership_and_interfaces_are_bound(self):
        self.assertEqual(self.contract["contract"], "lca_research_kernel.d18_core_architecture_contract/v1")
        modules = {row["id"]: row for row in self.contract["modules"]}
        self.assertEqual(set(modules), MODULE_IDS)
        self.assertEqual(
            [row["id"] for row in self.contract["modules"] if row["kind"] == "control_core"],
            ["research_orchestrator"],
        )
        self.assertEqual(
            [row["id"] for row in self.contract["modules"] if row["kind"] == "truth_plane"],
            ["project_record"],
        )
        for module_id, row in modules.items():
            self.assertTrue(row["owns"])
            self.assertTrue(row["must_not"])
            self.assertTrue(row["interface"])
            expected_status = (
                "implemented_focused_verified"
                if module_id
                in {"project_record", "model_gateway", "research_orchestrator"}
                else "contract_only"
            )
            self.assertEqual(row["implementation"]["status"], expected_status)
        self.assertEqual(
            modules["project_record"]["implementation"]["verification_test"],
            "tests/test_project_record_port.py",
        )
        self.assertEqual(
            modules["model_gateway"]["implementation"]["verification_test"],
            "tests/test_model_gateway.py",
        )
        self.assertEqual(
            modules["research_orchestrator"]["implementation"]["verification_test"],
            "tests/test_research_orchestrator.py",
        )

    def test_only_assurance_can_submit_an_accepted_project_record_write(self):
        rules = self.contract["dependency_rules"]
        self.assertEqual(rules["accepted_project_record_writers"], ["assurance_boundary"])
        self.assertEqual(
            rules["forbidden_direct_write_sources"],
            ["capability_gateway", "model_gateway", "optional_council_strategy"],
        )
        edges = {(row["from"], row["to"], row["channel"]) for row in rules["edges"]}
        self.assertIn(("assurance_boundary", "project_record", "accepted_change"), edges)
        self.assertNotIn(("model_gateway", "project_record", "accepted_change"), edges)
        self.assertNotIn(("capability_gateway", "project_record", "accepted_change"), edges)
        self.assertEqual(rules["model_output_status"], "candidate_only")
        self.assertEqual(rules["tool_output_status"], "sourced_result_not_fact")

    def test_frozen_case_and_paused_assets_cannot_become_core_dependencies(self):
        frozen = {row["cluster"]: row for row in self.contract["frozen_assets"]}
        self.assertEqual(
            frozen["paused_static_material_gap_work"]["disposition"],
            "freeze_uncommitted_pending_D18_mapping",
        )
        self.assertIn(
            "lca_research_kernel/static_material_gap_resolution.py",
            frozen["paused_static_material_gap_work"]["path_patterns"],
        )
        for cluster in (
            "council_default_and_research_council",
            "uslci_case_chain",
            "lifepo4_minimum_comparison",
        ):
            self.assertIn(cluster, frozen)
        for module in self.contract["modules"]:
            path = module["implementation"]["module_path"].lower()
            for forbidden in ("council", "uslci", "lifepo4", "nev"):
                self.assertNotIn(forbidden, path)

    def test_failure_semantics_are_typed_and_route_without_claim_inflation(self):
        failures = self.contract["failure_semantics"]
        self.assertEqual(
            {row["layer"] for row in failures},
            {"prompt", "context", "harness", "loop", "model"},
        )
        for row in failures:
            self.assertTrue(row["code"])
            self.assertTrue(row["result_status"])
            self.assertTrue(row["next_route"])
            self.assertFalse(row["may_promote_candidate_to_fact"])

    def test_contract_fails_closed_on_module_edge_or_basis_drift(self):
        drifted = copy.deepcopy(self.contract)
        drifted["modules"] = drifted["modules"][:-1]
        self.assertIn("D-18 module set is incomplete", self.validate(drifted))
        drifted = copy.deepcopy(self.contract)
        drifted["dependency_rules"]["accepted_project_record_writers"].append("model_gateway")
        self.assertIn("Project Record accepted-writer boundary drifted", self.validate(drifted))
        drifted = copy.deepcopy(self.contract)
        drifted["basis"]["top_level_architecture"]["sha256"] = "f" * 64
        self.assertIn("D-18 basis artifact drifted: docs/lca-agent-top-level-architecture.json", self.validate(drifted))
        drifted = copy.deepcopy(self.contract)
        drifted["modules"][5]["implementation"]["sha256"] = "f" * 64
        self.assertIn("D-18 implemented module drifted: project_record", self.validate(drifted))

    def test_unique_next_route_enters_lca_domain_kernel(self):
        self.assertEqual(self.contract["next_route"], "implement-d18-lca-domain-kernel")
        self.assertNotIn("next_routes", self.contract)


if __name__ == "__main__":
    unittest.main()
