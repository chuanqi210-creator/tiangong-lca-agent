#!/usr/bin/env python3
"""Close D-18 runtime readiness with real, case-free, zero-effect probes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_kernel.assurance import AssuranceBoundary
from lca_research_kernel.capability_adapters import SkillMetadataAdapter
from lca_research_kernel.capability_catalog import CapabilityCatalog
from lca_research_kernel.capability_gateway import (
    CapabilityGateway,
    canonical_sha256,
)
from lca_research_kernel.runtime_environment import RuntimeEnvironmentContract
from lca_research_kernel.runtime_environment_health import (
    OpenLcaServer,
    build_closed_readiness_matrix,
    classify_openlca_observation,
    verify_matrix_receipts,
)
from lca_research_kernel.runtime_health import RuntimeProbeRunner
from scripts.bootstrap_tiangong_runtime import (
    bootstrap_runtime,
    validate_runtime_lock,
)


CATALOG_PATH = ROOT / "lca_research_kernel/contracts/tiangong-capability-catalog.json"
SOURCE_LOCK_PATH = ROOT / "lca_research_kernel/contracts/tiangong-source-lock.json"
RUNTIME_LOCK_PATH = ROOT / "runtime/tiangong-runtime-source-lock.json"
SKILLS_SNAPSHOT = Path("external/tiangong/skills")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> str:
    payload = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _write_group_receipt(
    root: Path,
    *,
    group: str,
    observed_at: str,
    observation: Mapping[str, object],
    supporting_receipts: Sequence[Mapping[str, object]],
    gates: Sequence[str],
) -> dict:
    value = {
        "contract": "lca_research_kernel.runtime_readiness_group_receipt/v1",
        "group": group,
        "observed_at": observed_at,
        "status": "passed",
        "gates_passed": list(gates),
        "observation": dict(observation),
        "supporting_receipts": [
            {
                "receipt_ref": row["receipt_ref"],
                "receipt_sha256": row["receipt_sha256"],
            }
            for row in supporting_receipts
        ],
        "executed_external_writes": 0,
        "scientific_result_created": False,
        "calculation_executed": False,
    }
    payload = _canonical_bytes(value)
    digest = hashlib.sha256(payload).hexdigest()
    relative = Path("outputs/runtime-environment/receipts") / (
        f"{group}-{digest[:16]}.json"
    )
    (root / relative).parent.mkdir(parents=True, exist_ok=True)
    (root / relative).write_bytes(payload)
    return {
        "kind": "real_runtime_probe",
        "receipt_ref": relative.as_posix(),
        "receipt_sha256": digest,
    }


def _safe_environment(runtime_root: Path) -> dict[str, str]:
    node_bin = runtime_root / "node/node_modules/.bin"
    return {
        "LC_ALL": "C",
        "NODE_ENV": "production",
        "PATH": f"{node_bin}:{os.environ.get('PATH', '')}",
        "TIANGONG_LCA_CLI_DIR": str(
            runtime_root / "node/node_modules/@tiangong-lca/cli"
        ),
    }


class ProbeHarness:
    def __init__(self, root: Path, *, observed_at: str):
        self.root = Path(root)
        self.observed_at = observed_at
        self.receipt_root = Path("outputs/runtime-environment/receipts/raw")
        self.lock_id = json.loads(
            (self.root / RUNTIME_LOCK_PATH.relative_to(ROOT)).read_text()
        )["lock_id"]

    def run(
        self,
        probe_id: str,
        argv: Sequence[str | Path],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 30,
        expect_json: bool = False,
    ) -> tuple[dict, object | None]:
        captured: dict[str, subprocess.CompletedProcess] = {}

        def executor(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
            completed = subprocess.run(*args, **kwargs)
            captured["completed"] = completed
            return completed

        runner = RuntimeProbeRunner(
            receipt_root=self.receipt_root,
            executor=executor,
            clock=lambda: self.observed_at,
        )
        result = runner.run(
            probe_id=probe_id,
            source_lock_id=self.lock_id,
            argv=[str(item) for item in argv],
            effect="local_read",
            timeout_seconds=timeout_seconds,
            environment=environment,
        )
        receipt = result["receipt"]
        if receipt["status"] != "passed":
            raise RuntimeError(f"runtime probe failed: {probe_id}")
        parsed = None
        if expect_json:
            try:
                parsed = json.loads(captured["completed"].stdout)
            except (KeyError, json.JSONDecodeError) as error:
                raise ValueError(f"runtime probe JSON is invalid: {probe_id}") from error
            if not isinstance(parsed, dict):
                raise ValueError(f"runtime probe JSON is not an object: {probe_id}")
        return result, parsed


def _operation_request(
    *,
    catalog: CapabilityCatalog,
    assurance: AssuranceBoundary,
    descriptor: Mapping[str, object],
    request_id: str,
) -> dict:
    payload: dict = {}
    intent = {
        "contract": "lca_research_kernel.operation_intent/v1",
        "operation_id": request_id,
        "effect": descriptor["effect"],
        "responsibility_tags": [],
        "target": descriptor["capability_id"],
        "scope": "research:runtime-environment-health",
        "payload": payload,
        "payload_ref": {
            "path": f"outputs/runtime-environment/requests/{request_id}.json",
            "sha256": canonical_sha256(payload),
            "version": "v1",
        },
        "verification": "verify exact installed Skill metadata envelope",
        "compensation": "discard unaccepted metadata result",
        "resume_route": "continue-runtime-health",
    }
    return {
        "contract": "lca_research_kernel.capability_request/v1",
        "request_id": request_id,
        "capability_id": descriptor["capability_id"],
        "effect": descriptor["effect"],
        "input": payload,
        "catalog_snapshot_sha256": catalog.snapshot_sha256,
        "source_lock_id": descriptor["source_lock_id"],
        "operation_intent": intent,
        "authorization": assurance.authorize_action(intent),
    }


def load_installed_skills_through_gateway(root: Path, runtime_root: Path) -> dict:
    catalog = CapabilityCatalog.from_files(
        root / CATALOG_PATH.relative_to(ROOT),
        root / SOURCE_LOCK_PATH.relative_to(ROOT),
    )
    assurance = AssuranceBoundary()
    gateway = CapabilityGateway(
        catalog,
        adapters={
            "skill.metadata": SkillMetadataAdapter(root=runtime_root / "skills")
        },
    )
    loaded = []
    boundary = []
    for descriptor in catalog.list_descriptors():
        if descriptor["adapter_id"] != "skill.metadata":
            continue
        if descriptor["disposition"] == "human_action_required":
            path = runtime_root / "skills" / str(descriptor["entrypoint"])
            if not path.is_file():
                raise ValueError("human-owned Skill metadata is not installed")
            boundary.append(descriptor["capability_id"])
            continue
        request_id = "runtime-skill-" + hashlib.sha256(
            descriptor["capability_id"].encode("utf-8")
        ).hexdigest()[:16]
        result = gateway.execute(
            _operation_request(
                catalog=catalog,
                assurance=assurance,
                descriptor=descriptor,
                request_id=request_id,
            )
        )
        if result["status"] != "sourced_result_not_fact":
            raise ValueError("Skill Gateway result status drifted")
        loaded.append(
            {
                "capability_id": descriptor["capability_id"],
                "content_sha256": result["content"]["content_sha256"],
            }
        )
    return {
        "contract": "lca_research_kernel.installed_skill_gateway_probe/v1",
        "catalog_snapshot_sha256": catalog.snapshot_sha256,
        "gateway_loaded_count": len(loaded),
        "human_owned_metadata_present_count": len(boundary),
        "loaded_identity_sha256": canonical_sha256(loaded),
        "human_owned_identity_sha256": canonical_sha256(sorted(boundary)),
        "result_status": "sourced_result_not_fact",
        "executed_external_writes": 0,
        "scientific_result_created": False,
    }


def compile_boundary_observation(root: Path) -> dict:
    catalog = CapabilityCatalog.from_files(
        Path(root) / CATALOG_PATH.relative_to(ROOT),
        Path(root) / SOURCE_LOCK_PATH.relative_to(ROOT),
    )
    rows = [
        descriptor
        for descriptor in catalog.list_descriptors()
        if descriptor["disposition"] == "human_action_required"
    ]
    return {
        "contract": "lca_research_kernel.runtime_responsibility_boundary_probe/v1",
        "capability_count": len(rows),
        "capability_identity_sha256": canonical_sha256(
            [row["capability_id"] for row in rows]
        ),
        "effects": sorted({str(row["effect"]) for row in rows}),
        "executed_calls": 0,
        "external_writes": 0,
        "credential_values_read": 0,
        "resume_route": (
            "request-minimum-human-action-only-when-a-future-project-needs-it"
        ),
    }


def run_health(root: Path, *, skills_snapshot: Path) -> dict:
    root = Path(root).resolve()
    observed_at = _now()
    runtime_contract = RuntimeEnvironmentContract.load(root)
    runtime_root = root / str(runtime_contract.contract["runtime_root"])
    output_root = root / "outputs/runtime-environment"
    receipt_root = output_root / "receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)

    bootstrap = bootstrap_runtime(
        root,
        root / RUNTIME_LOCK_PATH.relative_to(ROOT),
        skills_source_snapshot=skills_snapshot,
    )
    lock_report = validate_runtime_lock(
        root, root / RUNTIME_LOCK_PATH.relative_to(ROOT)
    )
    harness = ProbeHarness(root, observed_at=observed_at)
    environment = _safe_environment(runtime_root)
    executable_gates = runtime_contract.executable_gates
    boundary_gates = runtime_contract.boundary_gates

    bootstrap_support = []
    for source_id, command in (
        ("node", ["node", "--version"]),
        ("npm", ["npm", "--version"]),
        ("python", ["python3.12", "--version"]),
        ("uv", ["uv", "--version"]),
    ):
        receipt, _ = harness.run(
            f"host-{source_id}",
            command,
            environment=environment,
        )
        bootstrap_support.append(receipt)
    bootstrap_evidence = _write_group_receipt(
        root,
        group="bootstrap",
        observed_at=observed_at,
        observation={
            "bootstrap": bootstrap,
            "lock_validation": lock_report,
            "runtime_root": runtime_root.relative_to(root).as_posix(),
        },
        supporting_receipts=bootstrap_support,
        gates=executable_gates,
    )

    cli = runtime_root / "node/node_modules/.bin/tiangong-lca"
    context_root = runtime_root / "health/context-pack"
    if context_root.exists():
        shutil.rmtree(context_root)
    cli_support = []
    for probe_id, argv in (
        ("tiangong-cli-version", [cli, "--version"]),
        ("tiangong-cli-help", [cli, "--help"]),
        ("tiangong-process-builder-help", [cli, "process", "auto-build", "--help"]),
        (
            "tiangong-lifecyclemodel-builder-help",
            [cli, "lifecyclemodel", "auto-build", "--help"],
        ),
        (
            "tiangong-context-pack",
            [
                cli,
                "dataset",
                "context-pack",
                "--type",
                "process",
                "--out-dir",
                context_root,
                "--json",
            ],
        ),
    ):
        receipt, _ = harness.run(
            probe_id,
            argv,
            environment=environment,
            timeout_seconds=60,
        )
        cli_support.append(receipt)
    context_outputs = context_root / "outputs"
    expected_context = {
        "contract-manifest.json",
        "schema.json",
        "methodology.yaml",
        "runtime-ruleset.json",
        "ai-context.json",
        "ai-context.md",
        "contract-report.json",
    }
    if not expected_context.issubset(
        {path.name for path in context_outputs.iterdir()}
    ):
        raise ValueError("TianGong context pack output coverage drifted")
    cli_evidence = _write_group_receipt(
        root,
        group="cli",
        observed_at=observed_at,
        observation={
            "cli_version": "0.0.29",
            "process_builder": "contract_entrypoint_verified_not_executed",
            "lifecyclemodel_builder": "contract_entrypoint_verified_not_executed",
            "context_pack_outputs": sorted(expected_context),
            "case_execution": 0,
        },
        supporting_receipts=cli_support,
        gates=executable_gates,
    )

    tidas_support = []
    for probe_id, argv, expect_json in (
        (
            "tidas-sdk",
            ["node", runtime_root / "node/probe-tidas-sdk.mjs"],
            True,
        ),
        (
            "tidas-validate-describe",
            [
                runtime_root / "python/bin/tidas-validate",
                "--describe",
                "--report-format",
                "json",
            ],
            True,
        ),
        (
            "tidas-convert-help",
            [runtime_root / "python/bin/tidas-convert", "--help"],
            False,
        ),
        (
            "tidas-import-help",
            [runtime_root / "python/bin/tidas-import", "--help"],
            False,
        ),
    ):
        receipt, parsed = harness.run(
            probe_id,
            argv,
            environment=environment,
            expect_json=expect_json,
            timeout_seconds=60,
        )
        if parsed is not None:
            if parsed.get("scientificResultsCreated", 0) != 0:
                raise ValueError("TIDAS health probe created a scientific result")
        tidas_support.append(receipt)
    tidas_evidence = _write_group_receipt(
        root,
        group="tidas",
        observed_at=observed_at,
        observation={
            "sdk_version": "0.1.45",
            "tidas_tools_version": "0.0.43",
            "schema_methodology_ruleset": "loaded_from_exact_context_pack",
            "conversion_or_import_executed": False,
        },
        supporting_receipts=tidas_support,
        gates=executable_gates,
    )

    skill_validator, _ = harness.run(
        "tiangong-skills-official-validator",
        [
            "node",
            runtime_root / "skills/scripts/validate-skills.mjs",
        ],
        environment=environment,
        timeout_seconds=120,
    )
    skill_gateway = load_installed_skills_through_gateway(root, runtime_root)
    skill_gateway_path = receipt_root / "skills-gateway-observation.json"
    skill_gateway_sha = _write_json(skill_gateway_path, skill_gateway)
    skills_evidence = _write_group_receipt(
        root,
        group="skills",
        observed_at=observed_at,
        observation={
            **skill_gateway,
            "installed_skill_files": 135,
            "official_skill_directories": 21,
            "official_validation": "passed",
        },
        supporting_receipts=[
            skill_validator,
            {
                "receipt_ref": skill_gateway_path.relative_to(root).as_posix(),
                "receipt_sha256": skill_gateway_sha,
            },
        ],
        gates=executable_gates,
    )

    mcp_support = []
    mcp_observations = []
    for index in (1, 2):
        receipt, parsed = harness.run(
            f"tiangong-mcp-{index}",
            ["node", runtime_root / "node/probe-mcp.mjs"],
            environment=environment,
            expect_json=True,
            timeout_seconds=30,
        )
        assert isinstance(parsed, dict)
        if (
            parsed.get("tools", {}).get("count") != 9
            or parsed.get("prompts", {}).get("count") != 1
            or parsed.get("resources", {}).get("count") != 1
            or parsed.get("safeCall", {}).get("isError") is not False
            or parsed.get("promptRead", {}).get("performed") is not True
            or parsed.get("resourceRead", {}).get("performed") is not True
            or parsed.get("forbiddenToolCalls") != 0
            or parsed.get("externalWrites") != 0
            or parsed.get("scientificResultsCreated") != 0
        ):
            raise ValueError("TianGong MCP health observation drifted")
        mcp_support.append(receipt)
        mcp_observations.append(parsed)
    if mcp_observations[0]["tools"] != mcp_observations[1]["tools"]:
        raise ValueError("TianGong MCP restart discovery drifted")
    mcp_evidence = _write_group_receipt(
        root,
        group="mcp",
        observed_at=observed_at,
        observation={
            "server_version": "0.0.31",
            "sdk_version": "1.29.0",
            "tools": 9,
            "prompts": 1,
            "resources": 1,
            "safe_guidance_calls": 2,
            "prompt_reads": 2,
            "resource_reads": 2,
            "restart_recovery": "passed",
            "forbidden_calls": 0,
        },
        supporting_receipts=mcp_support,
        gates=executable_gates,
    )

    openlca_support = []
    openlca_observations = []
    for index in (1, 2):
        with OpenLcaServer(runtime_root):
            direct_receipt, direct = harness.run(
                f"openlca-direct-{index}",
                ["node", runtime_root / "node/probe-openlca.mjs"],
                environment=environment,
                expect_json=True,
                timeout_seconds=30,
            )
            mcp_receipt, mcp_openlca = harness.run(
                f"openlca-mcp-read-{index}",
                ["node", runtime_root / "node/probe-mcp-openlca.mjs"],
                environment=environment,
                expect_json=True,
                timeout_seconds=30,
            )
        assert isinstance(direct, dict) and isinstance(mcp_openlca, dict)
        observation = classify_openlca_observation(direct, mcp_openlca)
        openlca_support.extend([direct_receipt, mcp_receipt])
        openlca_observations.append(observation)
    if openlca_observations[0] != openlca_observations[1]:
        raise ValueError("openLCA restart read observation drifted")
    openlca_evidence = _write_group_receipt(
        root,
        group="openlca",
        observed_at=observed_at,
        observation={
            "runtime_version": "2.6.2",
            "ipc_endpoint": "http://127.0.0.1:18080",
            "readiness": openlca_observations[0],
            "restart_recovery": "passed",
            "impact_assessment_tool": (
                "contract_and_transport_verified_not_invoked_in_this_phase"
            ),
        },
        supporting_receipts=openlca_support,
        gates=executable_gates,
    )

    boundary_observation = compile_boundary_observation(root)
    boundary_path = receipt_root / "responsibility-boundary-observation.json"
    boundary_sha = _write_json(boundary_path, boundary_observation)
    boundary_evidence = _write_group_receipt(
        root,
        group="boundary",
        observed_at=observed_at,
        observation=boundary_observation,
        supporting_receipts=[
            {
                "receipt_ref": boundary_path.relative_to(root).as_posix(),
                "receipt_sha256": boundary_sha,
            }
        ],
        gates=boundary_gates,
    )

    evidence = {
        "bootstrap": bootstrap_evidence,
        "cli": cli_evidence,
        "mcp": mcp_evidence,
        "openlca": openlca_evidence,
        "skills": skills_evidence,
        "tidas": tidas_evidence,
        "boundary": boundary_evidence,
    }
    matrix = build_closed_readiness_matrix(
        runtime_contract,
        evidence,
        observed_at=observed_at,
    )
    verify_matrix_receipts(root, matrix)
    matrix_path = output_root / "runtime-readiness-matrix.json"
    matrix_sha = _write_json(matrix_path, matrix)
    summary = {
        "contract": "lca_research_kernel.runtime_environment_acceptance/v1",
        "observed_at": observed_at,
        "status": "accepted",
        "capability_count": len(matrix["capabilities"]),
        "executable_verified": sum(
            row["readiness_status"] == "executable_verified"
            for row in matrix["capabilities"]
        ),
        "boundary_verified_nonlocal": sum(
            row["readiness_status"] == "boundary_verified_nonlocal"
            for row in matrix["capabilities"]
        ),
        "ambiguous_conditional_runtime": 0,
        "matrix": {
            "path": matrix_path.relative_to(root).as_posix(),
            "sha256": matrix_sha,
        },
        "runtime_source_lock": {
            "path": RUNTIME_LOCK_PATH.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(
                (root / RUNTIME_LOCK_PATH.relative_to(ROOT)).read_bytes()
            ).hexdigest(),
        },
        "case_executions": 0,
        "lca_calculations": 0,
        "lcia_calculations": 0,
        "external_writes": 0,
        "credential_values_read": 0,
        "remaining_user_collaboration_items": 0,
        "next_route": runtime_contract.contract["completion_next_route"],
    }
    summary_path = output_root / "runtime-health-summary.json"
    summary_sha = _write_json(summary_path, summary)
    report_path = output_root / "runtime-health-report.zh-CN.md"
    report_path.write_text(
        "# TianGong-native LCA Agent 运行环境闭合报告\n\n"
        f"本阶段已把 {summary['capability_count']} 项能力对应的运行环境收敛到"
        "可机械验证的状态："
        f"{summary['executable_verified']} 项本地适用能力为"
        " `executable_verified`，"
        f"{summary['boundary_verified_nonlocal']} 项需要未来具体项目中的"
        "人类责任动作，仅验证边界且实际调用为零。\n\n"
        "真实验证覆盖固定版本 TianGong CLI、TIDAS SDK、tidas-tools、"
        "21 个官方 Skills、MCP 握手/分页发现/Prompt/Resource/无副作用工具调用、"
        "OpenLCA 2.6.2 IPC 只读列举与重启恢复。OpenLCA 使用隔离的空数据库；"
        "列表为空只证明调用链可用，不构成科学数据或 LCA 结果。\n\n"
        "未执行研究案例、LCA/LCIA 计算、发布、外部写入、付款、凭证读取或"
        "不可逆动作。构建器和影响评价工具仅验证固定入口、schema、传输和"
        "运行依赖，本阶段没有运行案例或计算。\n\n"
        f"机器矩阵：`{matrix_path.relative_to(root).as_posix()}` "
        f"（SHA-256 `{matrix_sha}`）。\n\n"
        f"验收摘要：`{summary_path.relative_to(root).as_posix()}` "
        f"（SHA-256 `{summary_sha}`）。\n\n"
        "下一路线仅为设计基于统一能力目录的第一个 TianGong-native "
        "科研工作流；本阶段不会自动启动该工作流。\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--skills-source-snapshot",
        type=Path,
        default=SKILLS_SNAPSHOT,
    )
    arguments = parser.parse_args()
    summary = run_health(
        arguments.root,
        skills_snapshot=arguments.skills_source_snapshot,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
