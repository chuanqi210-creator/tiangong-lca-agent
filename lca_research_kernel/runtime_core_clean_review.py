"""Fresh-process, source-only P1/P2 review for the D-18 runtime core."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from .architecture_semantics import ArchitectureSemantics
from .model_runtime import load_model_runtime_contract
from .native_research_workflow import NativeResearchWorkflow


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.add(node.module or "")
    return result


def review_runtime_core(root: Path) -> dict:
    root = Path(root).resolve()
    p1 = []
    p2 = []
    checks = {}
    try:
        semantics = ArchitectureSemantics.load(
            root,
            root
            / "lca_research_kernel/contracts/d18-runtime-architecture-semantics.json",
        )
        checks["seven_module_architecture"] = len(semantics.module_ids) == 7
    except (OSError, ValueError) as error:
        checks["seven_module_architecture"] = False
        p1.append(f"architecture semantics invalid: {type(error).__name__}")
    try:
        NativeResearchWorkflow.load(
            root / "lca_research_kernel/contracts/native-research-workflow-v1.json"
        )
        workflow_source = (
            root / "lca_research_kernel/native_research_workflow.py"
        ).read_text(encoding="utf-8").lower()
        checks["workflow_case_neutral"] = not any(
            marker in workflow_source
            for marker in ("lifepo4", "freshwater", "uslci", "nev")
        )
        if not checks["workflow_case_neutral"]:
            p1.append("formal workflow contains product-specific routing")
    except (OSError, ValueError) as error:
        checks["workflow_case_neutral"] = False
        p1.append(f"native workflow invalid: {type(error).__name__}")
    model_runtime = None
    try:
        model_runtime = load_model_runtime_contract(root)
        checks["pinned_model_runtime"] = True
    except (OSError, ValueError) as error:
        checks["pinned_model_runtime"] = False
        p1.append(f"model runtime source lock invalid: {type(error).__name__}")
    gateway_imports = _imports(root / "lca_research_kernel/model_gateway.py") | _imports(
        root / "lca_research_kernel/capability_gateway.py"
    )
    checks["gateway_truth_separation"] = not any(
        value.endswith("project_record") or value.endswith(".project")
        for value in gateway_imports
    )
    if not checks["gateway_truth_separation"]:
        p1.append("replaceable gateway imports accepted project truth")
    product_source = (root / "lca_research_kernel/product_interface.py").read_text(
        encoding="utf-8"
    )
    orchestrator_source = (
        root / "lca_research_kernel/research_orchestrator.py"
    ).read_text(encoding="utf-8")
    checks["model_and_capability_execution_policy_bound"] = (
        product_source.count("with_execution_environment") >= 2
    )
    if not checks["model_and_capability_execution_policy_bound"]:
        p1.append("product path does not bind both replaceable gateways to execution policy")
    checks["native_workflow_orchestrator_integrated"] = (
        "NativeResearchWorkflow" in orchestrator_source
        and "route_native_workflow" in orchestrator_source
    )
    if not checks["native_workflow_orchestrator_integrated"]:
        p1.append("native workflow is not integrated into Research Orchestrator")
    trace_source = (root / "lca_research_kernel/run_trace.py").read_text(
        encoding="utf-8"
    )
    checks["run_trace_secret_rejection"] = all(
        marker in trace_source for marker in ("SENSITIVE_KEYS", "_assert_no_sensitive")
    )
    if not checks["run_trace_secret_rejection"]:
        p1.append("Run Trace lacks mechanical secret rejection")
    environment = json.loads(
        (
            root / "lca_research_kernel/contracts/project-execution-policy.json"
        ).read_text(encoding="utf-8")
    )
    checks["external_write_not_pre_authorized"] = (
        environment["network_rules"]["external_write_hosts"] == []
        and "external_write" in environment["effect_rules"]["human_owned"]
    )
    if not checks["external_write_not_pre_authorized"]:
        p1.append("execution policy pre-authorizes external writes")
    if isinstance(model_runtime, dict):
        catalog = model_runtime["provider_catalog"]
        policy = model_runtime["policy"]
        providers = {
            row["provider_id"]: row
            for row in catalog["providers"]
            if isinstance(row, dict) and isinstance(row.get("provider_id"), str)
        }
        checks["provider_catalog_driven"] = (
            "routes" not in policy
            and policy.get("provider_catalog", {}).get("path")
            == "lca_research_kernel/contracts/model-provider-catalog.json"
            and set(providers)
            == {"openai", "gemini", "anthropic", "zhipu"}
        )
        gate = catalog["current_validation_gate"]
        checks["current_provider_gate_nonblocking"] = (
            gate == ["openai", "gemini", "zhipu"]
            and all(
                providers[provider]["admission_state"] == "active_verified"
                for provider in ("openai", "gemini")
            )
            and providers["zhipu"]["admission_state"]
            in {"adapter_ready_not_verified", "active_verified"}
            and providers["zhipu"]["current_validation_required"] is True
            and providers["zhipu"]["future_activation_slot"] is False
            and providers["anthropic"]["admission_state"]
            == "adapter_ready_not_verified"
            and providers["anthropic"]["current_validation_required"] is False
            and providers["anthropic"]["future_activation_slot"] is True
        )
        allowed_hosts = set(environment["network_rules"]["external_read_hosts"])
        checks["provider_network_bound"] = all(
            row["network_host"] in allowed_hosts for row in providers.values()
        )
    else:
        checks["provider_catalog_driven"] = False
        checks["current_provider_gate_nonblocking"] = False
        checks["provider_network_bound"] = False
    for field in (
        "provider_catalog_driven",
        "current_provider_gate_nonblocking",
        "provider_network_bound",
    ):
        if not checks[field]:
            p1.append(f"{field} failed")
    report = {
        "contract": "lca_research_kernel.runtime_core_clean_review/v1",
        "status": "passed" if not p1 and not p2 else "failed",
        "p1_count": len(p1),
        "p2_count": len(p2),
        "p1_findings": p1,
        "p2_findings": p2,
        "checks": checks,
        "review_context": "fresh_process_source_and_contract_only",
    }
    report["review_sha256"] = _sha256(report)
    return report
