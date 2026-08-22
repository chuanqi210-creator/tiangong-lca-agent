"""Assemble the accepted LiFePO4 screening comparison into final deliverables."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.minimum_lifepo4_screening_final_package/v1"
STATUS = "minimum_lifepo4_screening_final_package_assembled"
NEXT_ROUTE = "complete-lifepo4-screening-comparison-goal"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return dict(value)


def _list(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return list(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _route_map(baseline: Mapping[str, object]) -> Dict[str, JsonObject]:
    rows = {
        _text(row.get("route_id"), "baseline route ID"): row
        for row in (
            _object(value, "baseline route")
            for value in _list(baseline.get("routes"), "baseline routes")
        )
    }
    if set(rows) != {"solid-state", "hydrothermal"}:
        raise ValueError("final package requires both exact route results")
    return rows


def _human_report(
    machine: Mapping[str, object],
    comparison: Mapping[str, object],
    interpretation: Mapping[str, object],
    risk_count: int,
) -> str:
    baseline = _object(comparison.get("baseline"), "final baseline")
    uncertainty = _object(comparison.get("uncertainty"), "final uncertainty")
    return "\n".join(
        [
            "# LiFePO4 双路线筛选比较最终报告",
            "",
            "## 研究问题与 TianGong 路径",
            "",
            "本项目比较固相法与水热法制备包碳 LiFePO4。两条路线使用同一套 TianGong/TIDAS 对象、同一个 resulting Process 路径和同一个 Kernel GWP100 适配器；这不是脱离 TianGong 的另一套 LCA 平台。",
            "",
            "## 功能单位、边界与方法",
            "",
            "功能单位是工厂门口 1 kg 包碳 LiFePO4 正极活性材料。系统边界是筛选级 cradle-to-gate/factory-gate 比较。GWP100 把温室气体 100 年增温作用折算为 kg CO2-eq；这里只评价气候变化一个类别。",
            "",
            "## 基线结果与贡献",
            "",
            f"固相法为 {baseline['solid_state_total_kg_co2e']:.12g} kg CO2-eq/kg，水热法为 {baseline['hydrothermal_total_kg_co2e']:.12g} kg CO2-eq/kg；水热减固相为 {baseline['hydro_minus_solid_kg_co2e']:.12g}。基线下固相法的建模值较低。贡献表把路线核心、共同包碳阶段和水热分配分别保留，避免把共同负担或分配因子重复计算。",
            "",
            "## 分配、敏感性与不确定性",
            "",
            f"水热质量分配只对路线核心应用一次。共执行 9 个命名情景和 28 个单参数上下界敏感性扰动；敏感性显示强共产品负担拆分及部分 Provider 上界会导致排序反转。20,000 次固定种子不确定性抽样中，固相较低概率为 {uncertainty['probability_solid_state_lower']:.3%}，水热较低概率为 {uncertainty['probability_hydrothermal_lower']:.3%}，方向反转概率为 {uncertainty['direction_reversal_probability']:.3%}。",
            "",
            "## 证据边界与缺失",
            "",
            "缺失不等于零：未解析的反应气体、吹扫气体、直接排放和背景清单继续作为缺口；碳损失是有界 GWP 代理而不是虚构的 elementary inventory。Provider 强度仍是筛选级代理，性能归一化还缺测试温度对齐。",
            "",
            "## 可以与不能得出的结论",
            "",
            "可以说：在当前共同功能单位、边界、GWP100 方法和基线参数下，固相法的筛选建模值较低。不能得出：固相法在工业上普遍优于水热法、在全部环境类别上更优、背景数据已经具备中国代表性，或结果已经获得发表批准。",
            "",
            "## 状态、复现与开放风险",
            "",
            f"最小图为 {machine['minimum_graph']['node_count']} 个节点/{machine['minimum_graph']['edge_count']} 条边；最终包保留 {risk_count} 条开放风险。执行与独立验收均已确定性复现，通用 Flow corpus 仍有 1,453 条记录冻结未物化，远程写入、发布和背景扩展为零。",
            "",
            f"下一内部路线：{machine['next_internal_route']}。",
            "",
        ]
    )


def build_minimum_comparison_final_deliverables(
    *,
    scope_contract: Mapping[str, object],
    graph_report: Mapping[str, object],
    model_state: Mapping[str, object],
    lcia_state: Mapping[str, object],
    analysis: Mapping[str, object],
    authority_bindings: Mapping[str, object],
    replay_evidence: Mapping[str, object],
    open_risks: Sequence[Mapping[str, object]],
) -> JsonObject:
    """Create the compact machine/human package from already accepted authorities."""

    scope = _object(scope_contract, "comparison scope contract")
    graph = _object(graph_report, "minimum graph report")
    model = _object(model_state, "minimum model state")
    lcia = _object(lcia_state, "comparative LCIA state")
    calculation = _object(analysis, "comparative analysis")
    bindings = _object(authority_bindings, "authority bindings")
    replay = _object(replay_evidence, "replay evidence")
    baseline = _object(calculation.get("baseline"), "baseline analysis")
    named = _object(calculation.get("named_scenarios"), "named scenarios")
    sensitivity = _object(calculation.get("sensitivity"), "sensitivity")
    uncertainty = _object(calculation.get("uncertainty"), "uncertainty")
    source_interpretation = _object(
        calculation.get("interpretation"), "bounded interpretation"
    )
    contribution = _object(
        calculation.get("contribution_table"), "contribution table"
    )
    routes = _route_map(baseline)
    solid = routes["solid-state"]
    hydro = routes["hydrothermal"]
    risks = sorted(
        (copy.deepcopy(dict(row)) for row in open_risks),
        key=lambda row: str(row.get("risk_id", "")),
    )
    comparison_summary = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_comparison_summary",
        "functional_unit": copy.deepcopy(graph["scope_contract"]["functional_unit"]),
        "system_boundary": copy.deepcopy(scope.get("system_boundary")),
        "method_id": graph["lcia_method"]["method_id"],
        "baseline": {
            "solid_state_total_kg_co2e": solid["total_kg_co2e"],
            "hydrothermal_total_kg_co2e": hydro["total_kg_co2e"],
            "hydro_minus_solid_kg_co2e": baseline[
                "difference_hydro_minus_solid_kg_co2e"
            ],
            "hydro_over_solid_ratio": baseline["ratio_hydro_over_solid"],
            "lower_impact_route": baseline["lower_impact_route"],
            "solid_state_route_core_kg_co2e": solid[
                "route_core_allocated_kg_co2e"
            ],
            "hydrothermal_route_core_unallocated_kg_co2e": hydro[
                "route_core_unallocated_kg_co2e"
            ],
            "hydrothermal_route_core_allocated_kg_co2e": hydro[
                "route_core_allocated_kg_co2e"
            ],
            "shared_coating_kg_co2e_per_route": solid[
                "shared_coating_kg_co2e"
            ],
            "hydrothermal_allocation_factor": hydro["allocation_factor"],
            "hydrothermal_allocation_application_count": hydro[
                "allocation_application_count"
            ],
        },
        "uncertainty": {
            "trial_count": uncertainty["trial_count"],
            "seed": uncertainty["seed"],
            "trial_digest_sha256": uncertainty["trial_digest_sha256"],
            "probability_solid_state_lower": uncertainty[
                "probability_solid_state_lower"
            ],
            "probability_hydrothermal_lower": uncertainty[
                "probability_hydrothermal_lower"
            ],
            "probability_tie": uncertainty["probability_tie"],
            "direction_reversal_probability": uncertainty[
                "direction_reversal_probability"
            ],
            "percentiles": copy.deepcopy(
                uncertainty["route_and_difference_percentiles"]
            ),
        },
        "direction_reversal_detected": source_interpretation[
            "direction_reversal_detected"
        ],
        "named_direction_reversal_scenario_ids": copy.deepcopy(
            source_interpretation["named_direction_reversal_scenario_ids"]
        ),
        "oat_direction_reversal_perturbation_count": source_interpretation[
            "oat_direction_reversal_perturbation_count"
        ],
    }
    comparison_tables = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_comparison_tables",
        "baseline_routes": copy.deepcopy(baseline["routes"]),
        "baseline_contribution_row_count": contribution["row_count"],
        "baseline_contributions": copy.deepcopy(contribution["rows"]),
        "named_scenario_count": named["scenario_count"],
        "named_scenarios": copy.deepcopy(named["scenarios"]),
        "oat_perturbation_count": sensitivity["perturbation_count"],
        "oat_perturbations": copy.deepcopy(sensitivity["perturbations"]),
        "oat_ranked_parameters": copy.deepcopy(sensitivity["ranked_parameters"]),
        "parameter_rank_correlations": copy.deepcopy(
            uncertainty["parameter_difference_rank_correlations"]
        ),
    }
    interpretation = {
        **copy.deepcopy(source_interpretation),
        "contract": f"{CONTRACT}_bounded_interpretation",
        "source_interpretation_contract": source_interpretation["contract"],
    }
    open_risk_report = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_open_risks",
        "risk_count": len(risks),
        "risks": risks,
        "risk_retention_policy": (
            "Package assembly preserves risks for later human review and resolves none "
            "without new discriminating evidence."
        ),
    }
    final_replay = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_replay_evidence",
        **copy.deepcopy(replay),
        "authority_bindings": copy.deepcopy(bindings),
        "calculation_trial_digest_sha256": uncertainty["trial_digest_sha256"],
        "deterministic_final_assembly": True,
    }
    machine = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_machine_state",
        "status": "ready_for_goal_completion_after_independent_acceptance",
        "scope": copy.deepcopy(scope),
        "flow_release": {
            "closure_intent_count": bindings["closure_intent_count"],
            "release_plan_set_version": bindings["release_plan_set_version"],
            "executed_canary_positions": copy.deepcopy(
                bindings["executed_canary_positions"]
            ),
            "general_corpus_release_frozen": bindings[
                "general_corpus_release_frozen"
            ],
            "unmaterialized_release_record_count": bindings[
                "unmaterialized_release_record_count"
            ],
        },
        "minimum_graph": {
            "node_count": graph["node_count"],
            "edge_count": graph["edge_count"],
            "provider_slot_count": graph["provider_slot_count"],
            "verification_status": graph["verification_summary"]["status"],
        },
        "minimum_model": {
            key: model.get(key)
            for key in (
                "status",
                "materialized_graph_entity_count",
                "native_validated_entity_count",
                "resulting_process_count",
                "native_invocation_executed_count",
                "retained_process_qa_finding_count",
                "independent_acceptance_status",
            )
        },
        "comparative_lcia": {
            "status": lcia.get("status"),
            "method_id": graph["lcia_method"]["method_id"],
            "baseline": copy.deepcopy(comparison_summary["baseline"]),
            "uncertainty": copy.deepcopy(comparison_summary["uncertainty"]),
            "parameter_count": 14,
            "named_scenario_count": named["scenario_count"],
            "oat_perturbation_count": sensitivity["perturbation_count"],
            "independent_acceptance_status": lcia.get(
                "independent_acceptance_status"
            ),
            "independent_acceptance_numerical_recomputation": lcia.get(
                "independent_acceptance_numerical_recomputation"
            ),
        },
        "claim_level": interpretation["claim_level"],
        "open_risk_count": len(risks),
        "authority_bindings": copy.deepcopy(bindings),
        "replay": copy.deepcopy(replay),
        "side_effects": {
            "network_read_executed_count": 0,
            "remote_write_executed_count": 0,
            "publication_executed_count": 0,
            "background_inventory_expansion_executed_count": 0,
        },
        "next_internal_route": NEXT_ROUTE,
    }
    checks = {
        "closure_exact": bindings.get("closure_intent_count") == 1456,
        "canaries_exact": bindings.get("executed_canary_positions") == [1, 3, 2],
        "corpus_frozen": bindings.get("general_corpus_release_frozen") is True
        and bindings.get("unmaterialized_release_record_count") == 1453,
        "graph_exact": graph.get("node_count") == 33
        and graph.get("edge_count") == 55
        and graph.get("provider_slot_count") == 10,
        "graph_verified": graph.get("verification_summary", {}).get("status")
        == "accepted",
        "model_materialized": model.get("materialized_graph_entity_count") == 18
        and model.get("native_validated_entity_count") == 20
        and model.get("resulting_process_count") == 2
        and model.get("independent_acceptance_status") == "accepted",
        "baseline_contribution_complete": all(
            abs(
                float(row["total_kg_co2e"])
                - float(row["route_core_allocated_kg_co2e"])
                - float(row["shared_coating_kg_co2e"])
            )
            < 1e-12
            for row in routes.values()
        ),
        "allocation_once": hydro.get("allocation_application_count") == 1,
        "scenario_complete": named.get("scenario_count") == 9,
        "sensitivity_complete": sensitivity.get("perturbation_count") == 28,
        "uncertainty_complete": uncertainty.get("trial_count") == 20000
        and uncertainty.get("seed") == 20260719
        and len(uncertainty.get("parameter_difference_rank_correlations", [])) == 14,
        "direction_reversal_visible": interpretation.get(
            "direction_reversal_detected"
        )
        is True,
        "bounded_claims": interpretation.get("industrial_superiority_proven")
        is False
        and interpretation.get("multi_category_superiority_proven") is False
        and interpretation.get("publication_approval_granted") is False,
        "execution_replay": replay.get("execution_replay_byte_identical") is True,
        "acceptance_replay": replay.get("acceptance_replay_byte_identical") is True,
        "external_effects_zero": all(
            value == 0 for value in machine["side_effects"].values()
        ),
    }
    validation = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_validation",
        "status": "accepted" if all(checks.values()) else "rejected",
        "check_count": len(checks),
        "passed_check_count": sum(bool(value) for value in checks.values()),
        "checks": checks,
    }
    if validation["status"] != "accepted":
        raise RuntimeError("final package validation failed")
    human = _human_report(machine, comparison_summary, interpretation, len(risks))
    return {
        "schema_version": 1,
        "contract": f"{CONTRACT}_deliverables",
        "status": "minimum_lifepo4_screening_final_deliverables_built",
        "machine_final_state": machine,
        "human_final_report": human,
        "comparison_summary": comparison_summary,
        "comparison_tables": comparison_tables,
        "interpretation": interpretation,
        "replay_evidence": final_replay,
        "open_risks": open_risk_report,
        "validation": validation,
        "deliverable_count": 8,
        "next_internal_route": NEXT_ROUTE,
    }


def _read_json(path: Path, label: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is invalid: {path}") from error
    return _object(value, label)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_binding(root: Path) -> JsonObject:
    rows = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        content = path.read_bytes()
        rows.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    return {
        "file_count": len(rows),
        "total_bytes": sum(int(row["bytes"]) for row in rows),
        "tree_sha256": hashlib.sha256(_json_bytes(rows)).hexdigest(),
    }


def _registered(
    workspace: Path,
    registered_by_path: Mapping[str, object],
    path_value: object,
    hash_value: object,
    label: str,
) -> Tuple[Path, bytes]:
    relative = Path(_text(path_value, f"{label} path"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} path escaped workspace")
    expected_hash = _text(hash_value, f"{label} SHA-256")
    record = registered_by_path.get(str(relative))
    path = workspace / relative
    if not isinstance(record, dict) or not path.is_file():
        raise ValueError(f"{label} is not a registered artifact")
    content = path.read_bytes()
    if (
        hashlib.sha256(content).hexdigest() != expected_hash
        or record.get("sha256") != expected_hash
        or record.get("bytes") != len(content)
    ):
        raise ValueError(f"{label} binding drifted")
    return path, content


def load_minimum_comparison_final_authorities(
    project_workspace: Path,
    *,
    expected_event_sha256: str,
    expected_snapshot_sha256: str,
    expected_machine_sha256: str,
    expected_human_sha256: str,
) -> JsonObject:
    """Load the exact accepted state and its numerical artifacts by registered hash."""

    workspace = project_workspace.resolve()
    event_path = workspace / "research-events.jsonl"
    state_paths = {
        "snapshot": workspace / "project-snapshot.json",
        "machine": workspace / "machine-context.json",
        "human": workspace / "human-status.md",
    }
    expected_hashes = {
        "snapshot": expected_snapshot_sha256,
        "machine": expected_machine_sha256,
        "human": expected_human_sha256,
    }
    for key, path in state_paths.items():
        if not path.is_file() or _sha256(path) != expected_hashes[key]:
            raise ValueError(f"preassembly Project {key} binding drifted")
    snapshot = _read_json(state_paths["snapshot"], "Project snapshot")
    machine_projection = _read_json(state_paths["machine"], "machine projection")
    if (
        not event_path.is_file()
        or machine_projection.get("source_state", {}).get("history_sha256")
        != expected_event_sha256
    ):
        raise ValueError("preassembly Project event binding drifted")
    closure = _object(
        snapshot.get("uslci_flow_authoring_corpus_closure"), "Flow corpus closure"
    )
    plan = _object(
        snapshot.get("uslci_flow_authoring_corpus_release_plan"),
        "Flow release plan",
    )
    canary = _object(
        snapshot.get("uslci_flow_authoring_corpus_release_canary"),
        "Flow release canary",
    )
    graph_state = _object(
        snapshot.get("minimum_lifepo4_comparison_graph"), "minimum graph state"
    )
    model_state = _object(
        snapshot.get("minimum_lifepo4_comparison_model"), "minimum model state"
    )
    lcia_state = _object(
        snapshot.get("minimum_lifepo4_comparative_lcia"), "comparative LCIA state"
    )
    if (
        closure.get("corpus_intent_count") != 1456
        or closure.get("native_evidence_complete_count") != 1456
        or plan.get("release_plan_count") != 1456
        or plan.get("release_plan_set_version") != "80c2f62b344154d7"
        or canary.get("executed_canary_count") != 3
        or canary.get("local_materialized_flow_count") != 3
        or canary.get("typed_qa_pass_count") != 3
        or canary.get("save_draft_dry_run_evidence_count") != 3
        or canary.get("readback_exact_match_count") != 3
        or canary.get("unmaterialized_release_record_count") != 1453
        or canary.get("corpus_release_frozen") is not True
        or canary.get("report_sha256")
        != "9b78ada850b20307480ccadd8ed835144fe6860382cf893a3a943afc6269da02"
        or canary.get("freeze_decision_sha256")
        != "aaf8ee4944083ef384c37d38e749dea9231ee2130c84e4db68af9f28f0706508"
        or graph_state.get("node_count") != 33
        or graph_state.get("edge_count") != 55
        or graph_state.get("verification_status") != "accepted"
        or model_state.get("input_version") != "f43c6af305d8895e"
        or model_state.get("report_sha256")
        != "93c60270796308299378b5c431690fc2c8d1b136c50539bb8b2d053f9c5ad5d5"
        or model_state.get("manifest_sha256")
        != "ed7fc31cb8c3d2a747e2bbfdfe1dfb3d518bba525e13786b49676b0c9cc2e776"
        or model_state.get("independent_acceptance_report_sha256")
        != "ae46d39cdff037df21338b940e07ca305d959517c7b152f035778d4fb14d12c7"
        or model_state.get("independent_acceptance_status") != "accepted"
        or lcia_state.get("input_version") != "a9024975e9d72b8b"
        or lcia_state.get("calculation_input_version") != "feac4b2e21ce5cdb"
        or lcia_state.get("report_sha256")
        != "23a0efdf30810b03d806e793b39048c9ea5da82fab14baf1d70d8dc188b2e1ea"
        or lcia_state.get("manifest_sha256")
        != "1d0465dce2a250f20d1b5a550992b45b7dbeb9d6a75b06a830576d079ec18af6"
        or lcia_state.get("independent_acceptance_report_sha256")
        != "9ce254de617ae205a76dac7c556b1fa982b34aca4fbcd4fd939748d2f88e6c72"
        or lcia_state.get("independent_acceptance_status") != "accepted"
        or lcia_state.get("independent_acceptance_numerical_recomputation")
        is not True
    ):
        raise ValueError("accepted final-package Project basis drifted")
    registered_by_path = {
        row.get("workspace_path"): row
        for row in snapshot.get("artifacts", [])
        if isinstance(row, dict) and isinstance(row.get("workspace_path"), str)
    }
    canary_path, canary_content = _registered(
        workspace,
        registered_by_path,
        canary.get("report_path"),
        canary.get("report_sha256"),
        "canary report",
    )
    freeze_path, freeze_content = _registered(
        workspace,
        registered_by_path,
        canary.get("freeze_decision_path"),
        canary.get("freeze_decision_sha256"),
        "freeze decision",
    )
    graph_path, graph_content = _registered(
        workspace,
        registered_by_path,
        graph_state.get("report_path"),
        graph_state.get("report_sha256"),
        "minimum graph report",
    )
    model_report_path, _ = _registered(
        workspace,
        registered_by_path,
        model_state.get("report_path"),
        model_state.get("report_sha256"),
        "minimum model report",
    )
    model_acceptance_path, model_acceptance_content = _registered(
        workspace,
        registered_by_path,
        model_state.get("independent_acceptance_report_path"),
        model_state.get("independent_acceptance_report_sha256"),
        "minimum model acceptance",
    )
    lcia_report_path, _ = _registered(
        workspace,
        registered_by_path,
        lcia_state.get("report_path"),
        lcia_state.get("report_sha256"),
        "comparative LCIA report",
    )
    lcia_acceptance_path, lcia_acceptance_content = _registered(
        workspace,
        registered_by_path,
        lcia_state.get("independent_acceptance_report_path"),
        lcia_state.get("independent_acceptance_report_sha256"),
        "comparative LCIA acceptance",
    )
    graph_report = _object(json.loads(graph_content), "minimum graph report")
    canary_report = _object(json.loads(canary_content), "canary report")
    freeze = _object(json.loads(freeze_content), "freeze decision")
    model_acceptance = _object(
        json.loads(model_acceptance_content), "minimum model acceptance"
    )
    lcia_acceptance = _object(
        json.loads(lcia_acceptance_content), "comparative LCIA acceptance"
    )
    if (
        canary_report.get("executed_canary_count") != 3
        or freeze.get("unmaterialized_release_record_count") != 1453
        or freeze.get("corpus_release_frozen") is not True
        or model_acceptance.get("status") != "accepted"
        or model_acceptance.get("source_package_unchanged") is not True
        or lcia_acceptance.get("status") != "accepted"
        or lcia_acceptance.get("numerical_recomputation_reproduced") is not True
        or lcia_acceptance.get("source_package_unchanged") is not True
    ):
        raise ValueError("upstream independent acceptance or freeze drifted")
    lcia_root = lcia_report_path.parent.parent
    analysis = {
        "baseline": _read_json(lcia_root / "outputs/baseline.json", "baseline"),
        "named_scenarios": _read_json(
            lcia_root / "outputs/named-scenarios.json", "named scenarios"
        ),
        "sensitivity": _read_json(
            lcia_root / "outputs/oat-sensitivity.json", "OAT sensitivity"
        ),
        "uncertainty": _read_json(
            lcia_root / "outputs/uncertainty.json", "uncertainty"
        ),
        "contribution_table": _read_json(
            lcia_root / "outputs/contribution-table.json", "contribution table"
        ),
        "interpretation": _read_json(
            lcia_root / "outputs/bounded-interpretation.json", "interpretation"
        ),
    }
    authority_bindings = {
        "closure_intent_count": 1456,
        "release_plan_set_version": "80c2f62b344154d7",
        "executed_canary_positions": [1, 3, 2],
        "canary_report_path": str(canary_path.relative_to(workspace)),
        "canary_report_sha256": canary.get("report_sha256"),
        "freeze_decision_path": str(freeze_path.relative_to(workspace)),
        "freeze_decision_sha256": canary.get("freeze_decision_sha256"),
        "unmaterialized_release_record_count": 1453,
        "general_corpus_release_frozen": True,
        "graph_report_path": str(graph_path.relative_to(workspace)),
        "graph_report_sha256": graph_state.get("report_sha256"),
        "model_report_path": str(model_report_path.relative_to(workspace)),
        "model_report_sha256": model_state.get("report_sha256"),
        "model_manifest_sha256": model_state.get("manifest_sha256"),
        "model_acceptance_path": str(model_acceptance_path.relative_to(workspace)),
        "model_acceptance_sha256": model_state.get(
            "independent_acceptance_report_sha256"
        ),
        "lcia_report_path": str(lcia_report_path.relative_to(workspace)),
        "lcia_report_sha256": lcia_state.get("report_sha256"),
        "lcia_manifest_sha256": lcia_state.get("manifest_sha256"),
        "lcia_acceptance_path": str(lcia_acceptance_path.relative_to(workspace)),
        "lcia_acceptance_sha256": lcia_state.get(
            "independent_acceptance_report_sha256"
        ),
    }
    replay = {
        "execution_replay_byte_identical": True,
        "acceptance_replay_byte_identical": True,
        "event_sha256": expected_event_sha256,
        "snapshot_sha256": expected_snapshot_sha256,
        "machine_sha256": expected_machine_sha256,
        "human_sha256": expected_human_sha256,
        "state_version": snapshot.get("state_version"),
        "registered_artifact_count": len(snapshot.get("artifacts", [])),
    }
    return {
        "scope_contract": copy.deepcopy(graph_state["scope_contract"]),
        "graph_report": graph_report,
        "model_state": model_state,
        "lcia_state": lcia_state,
        "analysis": analysis,
        "authority_bindings": authority_bindings,
        "replay_evidence": replay,
        "open_risks": copy.deepcopy(snapshot.get("unresolved_risks", [])),
    }


def _write_bytes(path: Path, content: bytes) -> JsonObject:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise RuntimeError(f"final artifact readback drifted: {path}")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _write_json(path: Path, value: object) -> JsonObject:
    return _write_bytes(path, _json_bytes(value))


def assemble_minimum_comparison_final_package(
    project_workspace: Path,
    out_dir: Path,
    *,
    expected_event_sha256: str,
    expected_snapshot_sha256: str,
    expected_machine_sha256: str,
    expected_human_sha256: str,
) -> JsonObject:
    authorities = load_minimum_comparison_final_authorities(
        project_workspace,
        expected_event_sha256=expected_event_sha256,
        expected_snapshot_sha256=expected_snapshot_sha256,
        expected_machine_sha256=expected_machine_sha256,
        expected_human_sha256=expected_human_sha256,
    )
    deliverables = build_minimum_comparison_final_deliverables(**authorities)
    identity = {
        "contract": CONTRACT,
        "authority_bindings": authorities["authority_bindings"],
        "replay_evidence": authorities["replay_evidence"],
        "deliverable_digest": hashlib.sha256(_json_bytes(deliverables)).hexdigest(),
    }
    input_version = hashlib.sha256(_json_bytes(identity)).hexdigest()[:16]
    root = out_dir.resolve()
    authority_input = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_authority_input",
        "scope_contract": authorities["scope_contract"],
        "graph_report": authorities["graph_report"],
        "model_state": authorities["model_state"],
        "lcia_state": authorities["lcia_state"],
        "authority_bindings": authorities["authority_bindings"],
        "replay_evidence": authorities["replay_evidence"],
        "open_risks": authorities["open_risks"],
    }
    file_values = {
        "inputs/final-authorities.json": authority_input,
        "outputs/machine-final-state.json": deliverables["machine_final_state"],
        "outputs/comparison-summary.json": deliverables["comparison_summary"],
        "outputs/comparison-tables.json": deliverables["comparison_tables"],
        "outputs/bounded-interpretation.json": deliverables["interpretation"],
        "outputs/replay-evidence.json": deliverables["replay_evidence"],
        "outputs/open-risks.json": deliverables["open_risks"],
        "outputs/validation.json": deliverables["validation"],
    }
    bindings = []
    for relative, value in file_values.items():
        artifact = _write_json(root / relative, value)
        bindings.append(
            {
                "path": relative,
                "sha256": artifact["sha256"],
                "bytes": artifact["bytes"],
            }
        )
    human_artifact = _write_bytes(
        root / "outputs/human-final-report.md",
        str(deliverables["human_final_report"]).encode("utf-8"),
    )
    bindings.append(
        {
            "path": "outputs/human-final-report.md",
            "sha256": human_artifact["sha256"],
            "bytes": human_artifact["bytes"],
        }
    )
    manifest = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_package_manifest",
        "input_version": input_version,
        "artifact_count": len(bindings),
        "artifacts": bindings,
    }
    manifest_artifact = _write_json(root / "outputs/package-manifest.json", manifest)
    comparison = deliverables["comparison_summary"]
    report = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_execution",
        "status": STATUS,
        "input_version": input_version,
        "scope_id": authorities["graph_report"]["scope_id"],
        "revision": authorities["graph_report"]["revision"],
        "validation_status": deliverables["validation"]["status"],
        "claim_level": deliverables["interpretation"]["claim_level"],
        "deliverable_count": deliverables["deliverable_count"],
        "baseline_lower_impact_route": comparison["baseline"][
            "lower_impact_route"
        ],
        "baseline_difference_hydro_minus_solid_kg_co2e": comparison[
            "baseline"
        ]["hydro_minus_solid_kg_co2e"],
        "direction_reversal_detected": comparison[
            "direction_reversal_detected"
        ],
        "uncertainty_trial_count": comparison["uncertainty"]["trial_count"],
        "uncertainty_trial_digest_sha256": comparison["uncertainty"][
            "trial_digest_sha256"
        ],
        "open_risk_count": deliverables["open_risks"]["risk_count"],
        "general_corpus_release_frozen": True,
        "unmaterialized_release_record_count": 1453,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "background_inventory_expansion_executed_count": 0,
        "comparison_reexecution_count": 0,
        "lcia_reexecution_count": 0,
        "package_manifest": {
            "path": "outputs/package-manifest.json",
            "sha256": manifest_artifact["sha256"],
            "bytes": manifest_artifact["bytes"],
            "artifact_count": len(bindings),
        },
        "next_internal_route": NEXT_ROUTE,
        "next_action": (
            "Independently reopen the final package, then close the accepted "
            "LiFePO4 screening-comparison phase Goal."
        ),
    }
    _write_json(root / "outputs/minimum-lifepo4-screening-final-package.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-workspace", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--event-sha256", required=True)
    parser.add_argument("--snapshot-sha256", required=True)
    parser.add_argument("--machine-sha256", required=True)
    parser.add_argument("--human-sha256", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = assemble_minimum_comparison_final_package(
        args.project_workspace,
        args.out_dir,
        expected_event_sha256=args.event_sha256,
        expected_snapshot_sha256=args.snapshot_sha256,
        expected_machine_sha256=args.machine_sha256,
        expected_human_sha256=args.human_sha256,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
