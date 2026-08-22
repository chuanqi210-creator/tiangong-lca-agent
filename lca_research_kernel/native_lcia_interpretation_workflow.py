"""Project-native persistence for the bounded native-LCIA interpretation workflow."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

from .native_lcia_interpretation import (
    compile_native_lcia_profile,
    validate_native_lcia_profile,
)


PACKAGE_CONTRACT = "lca_research_kernel.native_lcia_interpretation_package/v1"
ACCEPTANCE_CONTRACT = (
    "lca_research_kernel.native_lcia_interpretation_acceptance/v1"
)
FUNCTIONAL_UNIT_CONTRACT = (
    "lca_research_kernel.native_lcia_functional_unit_binding/v1"
)
CONTEXT_FIELDS = {
    "work_item_id",
    "work_item_content_sha256",
    "lifecycle_basis_ref",
    "authority_receipt_ref",
    "input_artifacts",
    "outcome_contract",
}
INPUT_ROLES = {
    "native_lcia_bound_result",
    "native_lcia_interpretation_contract",
    "native_lcia_functional_unit_binding",
}
ARTIFACT_ROLES = {
    "native_lcia_interpretation_profile",
    "native_lcia_interpretation_report",
    "native_lcia_interpretation_package",
    "native_lcia_interpretation_acceptance",
}
EVENT_TYPES = {
    "research.native_lcia_interpretation.profile.compiled",
    "research.native_lcia_interpretation.package.assembled",
    "research.native_lcia_interpretation.package.independently_accepted",
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _pretty_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _digest(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _exact_sha(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be an exact lowercase SHA-256")
    return value


def _registered_ref(
    project: object,
    value: object,
    label: str,
    *,
    expected_role: str | None = None,
) -> tuple[dict, bytes]:
    if not isinstance(value, Mapping) or set(value) != {"role", "path", "sha256", "bytes"}:
        raise ValueError(f"{label} must be an exact registered artifact reference")
    role = value.get("role")
    path_value = value.get("path")
    digest = _exact_sha(value.get("sha256"), f"{label}.sha256")
    size = value.get("bytes")
    if (
        not isinstance(role, str)
        or not role
        or (expected_role is not None and role != expected_role)
        or not isinstance(path_value, str)
        or not path_value
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
    ):
        raise ValueError(f"{label} identity or role is invalid")
    registrations = []
    for event in project.events():
        if event.get("event_type") != "artifact.registered" or event.get("actor") != "controller":
            continue
        payload = event.get("payload")
        if isinstance(payload, Mapping) and payload.get("path") == path_value:
            registrations.append(payload)
    if not registrations:
        raise ValueError(f"{label} is not registered in the Project Artifact Bus")
    registration = registrations[-1]
    if (
        registration.get("artifact_role") != role
        or registration.get("sha256") != digest
        or registration.get("bytes") != size
    ):
        raise RuntimeError(f"{label} registration drifted")
    workspace_path = registration.get("workspace_path")
    if not isinstance(workspace_path, str) or not workspace_path:
        raise RuntimeError(f"{label} registered path is missing")
    workspace = project.workspace.resolve()
    path = (workspace / workspace_path).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise RuntimeError(f"{label} registered path escapes the Project") from error
    if not path.is_file():
        raise RuntimeError(f"{label} registered artifact is missing")
    content = path.read_bytes()
    if len(content) != size or _sha256(content) != digest:
        raise RuntimeError(f"{label} registered artifact drifted")
    return dict(value), content


def _load_json(content: bytes, label: str) -> dict:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _inputs(project: object, context: object) -> dict:
    if not isinstance(context, Mapping) or set(context) != CONTEXT_FIELDS:
        raise ValueError("Director work-item context fields are incomplete or unsupported")
    work_item_id = context.get("work_item_id")
    if not isinstance(work_item_id, str) or not work_item_id.strip():
        raise ValueError("Director work-item context work_item_id is missing")
    work_item_content_sha256 = _exact_sha(
        context.get("work_item_content_sha256"), "work_item_content_sha256"
    )
    lifecycle_ref, _ = _registered_ref(
        project,
        context.get("lifecycle_basis_ref"),
        "lifecycle_basis_ref",
        expected_role="lifecycle_basis",
    )
    authority_ref, _ = _registered_ref(
        project,
        context.get("authority_receipt_ref"),
        "authority_receipt_ref",
        expected_role="authority_receipt",
    )
    raw_inputs = context.get("input_artifacts")
    if not isinstance(raw_inputs, list) or len(raw_inputs) != len(INPUT_ROLES):
        raise ValueError("native LCIA workflow requires exactly three input artifacts")
    by_role: dict[str, dict] = {}
    contents: dict[str, bytes] = {}
    for index, raw in enumerate(raw_inputs):
        if not isinstance(raw, Mapping):
            raise ValueError(f"input_artifacts[{index}] is invalid")
        role = raw.get("role")
        if not isinstance(role, str) or role not in INPUT_ROLES or role in by_role:
            raise ValueError("native LCIA workflow input roles are missing or duplicated")
        ref, content = _registered_ref(
            project, raw, f"input_artifacts[{index}]", expected_role=role
        )
        by_role[role] = ref
        contents[role] = content
    if set(by_role) != INPUT_ROLES:
        raise ValueError("native LCIA workflow input roles are incomplete")
    outcome = context.get("outcome_contract")
    if not isinstance(outcome, Mapping) or outcome.get("contract") != PACKAGE_CONTRACT:
        raise ValueError("native LCIA workflow outcome contract is unsupported")
    context_identity = {
        "work_item_id": work_item_id.strip(),
        "work_item_content_sha256": work_item_content_sha256,
        "lifecycle_basis_ref": lifecycle_ref,
        "authority_receipt_ref": authority_ref,
        "input_artifacts": [by_role[role] for role in sorted(by_role)],
        "outcome_contract": dict(outcome),
    }
    return {
        "context": context_identity,
        "input_version": _digest(context_identity),
        "refs": by_role,
        "bound_result": _load_json(
            contents["native_lcia_bound_result"], "native LCIA bound result"
        ),
        "interpretation_contract": _load_json(
            contents["native_lcia_interpretation_contract"],
            "native LCIA interpretation contract",
        ),
        "functional_unit": _load_json(
            contents["native_lcia_functional_unit_binding"],
            "native LCIA functional-unit binding",
        ),
    }


def _validate_functional_unit(value: Mapping[str, object], process_exact_ref: object) -> dict:
    if value.get("contract") != FUNCTIONAL_UNIT_CONTRACT:
        raise ValueError("native LCIA functional-unit contract drifted")
    if value.get("process_exact_ref") != process_exact_ref:
        raise ValueError("native LCIA functional-unit process identity drifted")
    status = value.get("status")
    if status == "exact_human_readable_functional_unit":
        if not isinstance(value.get("amount"), str) or not isinstance(value.get("unit"), str):
            raise ValueError("native LCIA exact functional unit is incomplete")
    elif status == "explicit_human_readable_unit_gap":
        if (
            not isinstance(value.get("amount"), str)
            or value.get("unit") != "unresolved"
            or not isinstance(value.get("reference_flow_exact_ref"), str)
            or not isinstance(value.get("reference_flow_property_binding"), Mapping)
        ):
            raise ValueError("native LCIA functional-unit gap binding is incomplete")
    else:
        raise ValueError("native LCIA functional-unit status is unsupported")
    return dict(value)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise RuntimeError(f"native LCIA workflow artifact drifted: {path.name}")
        return
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _register(
    project: object,
    *,
    relative: str,
    role: str,
    content: bytes,
    input_version: str,
) -> dict:
    if role not in ARTIFACT_ROLES:
        raise ValueError("native LCIA workflow artifact role is unsupported")
    path = project.workspace / relative
    _atomic_write(path, content)
    reopened = path.read_bytes()
    digest = _sha256(reopened)
    if reopened != content or len(reopened) != len(content):
        raise RuntimeError("native LCIA workflow artifact verification failed")
    payload = {
        "artifact_id": f"native-lcia-{role}-{digest[:20]}",
        "activity_id": f"native-lcia-interpretation-{input_version[:20]}",
        "skill_ref": "native-lcia-interpretation-workflow/v1",
        "kind": "native_lcia_interpretation",
        "artifact_role": role,
        "path": relative,
        "workspace_path": relative,
        "sha256": digest,
        "bytes": len(reopened),
    }
    project.record(
        "artifact.registered",
        payload,
        actor="controller",
        idempotency_key=f"native-lcia:{input_version}:artifact:{role}",
    )
    return {
        "artifact_path": relative,
        "artifact_sha256": digest,
        "artifact_bytes": len(reopened),
    }


def _projected(project: object, key: str, input_version: str) -> dict | None:
    value = project.materialize_views().get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping) or value.get("input_version") != input_version:
        raise RuntimeError("native LCIA workflow Project state belongs to another input")
    ref = {
        "role": key,
        "path": value.get("artifact_path"),
        "sha256": value.get("artifact_sha256"),
        "bytes": value.get("artifact_bytes"),
    }
    _registered_ref(project, ref, key, expected_role=key)
    return dict(value)


def _artifact_json(project: object, state: Mapping[str, object], role: str) -> dict:
    ref = {
        "role": role,
        "path": state.get("artifact_path"),
        "sha256": state.get("artifact_sha256"),
        "bytes": state.get("artifact_bytes"),
    }
    _, content = _registered_ref(project, ref, role, expected_role=role)
    return _load_json(content, role)


def compile_native_lcia_interpretation_profile(project: object, context: object) -> dict:
    """Compile and register one exact native-LCIA scientific profile."""

    inputs = _inputs(project, context)
    existing = _projected(
        project, "native_lcia_interpretation_profile", inputs["input_version"]
    )
    if existing is not None:
        return existing
    profile = compile_native_lcia_profile(
        inputs["bound_result"], inputs["interpretation_contract"]
    )
    root = f"artifacts/native-lcia-interpretation/{inputs['input_version'][:20]}"
    artifact = _register(
        project,
        relative=f"{root}/profile.json",
        role="native_lcia_interpretation_profile",
        content=_pretty_bytes(profile),
        input_version=inputs["input_version"],
    )
    state = {
        "status": "native_lcia_interpretation_profile_compiled",
        "input_version": inputs["input_version"],
        "work_item_id": inputs["context"]["work_item_id"],
        "work_item_content_sha256": inputs["context"]["work_item_content_sha256"],
        "source_ref": inputs["refs"]["native_lcia_bound_result"],
        "indicator_count": len(profile["indicators"]),
        "relation_check_count": len(profile["relation_checks"]),
        "zero_signal_count": len(profile["zero_signals"]),
        "profile_path": artifact["artifact_path"],
        "profile_sha256": artifact["artifact_sha256"],
        **artifact,
    }
    project.record(
        "research.native_lcia_interpretation.profile.compiled",
        {"profile": state},
        actor="controller",
        idempotency_key=f"native-lcia:{inputs['input_version']}:profile:compiled",
    )
    project.materialize_views()
    return state


def _render_report(package: Mapping[str, object]) -> bytes:
    profile = package["interpretation_profile"]
    functional_unit = package["functional_unit"]
    lines = [
        "# TianGong 原生 LCIA 科学解读",
        "",
        "## 读者先看",
        "",
        (
            "本报告逐项解释 TianGong 已发布的原生 LCIA 结果。它保留官方方法身份、"
            "版本、数值与单位，不重新计算或修正 TianGong 数据。"
        ),
        (
            f"结果对应的功能单位绑定为：{functional_unit['amount']} "
            f"{functional_unit['unit']}（状态：{functional_unit['status']}）。"
        ),
        "不同指标的单位和含义不同，不能跨单位按数值大小排序。",
        "",
        "## 25 项官方指标",
        "",
        "| TianGong 方法（精确版本） | 指标名称 | 官方值 | 官方单位 |",
        "|---|---|---:|---|",
    ]
    for row in profile["indicators"]:
        lines.append(
            f"| {row['method_exact_ref']} | {row['name']} | {row['value']} | "
            f"{row['resolved_unit']} |"
        )
    lines.extend(
        [
            "",
            "## 冻结的内部一致性关系",
            "",
            "以下四组检查只在各自兼容单位内核对总项与分项，不构成跨指标加权。",
            "",
            "| 关系 | 总项值 | 分项和 | 容差 | 结论 |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for row in profile["relation_checks"]:
        lines.append(
            f"| {row['relation_id']} | {row['total_value']} | "
            f"{row['component_sum']} | {row['tolerance_limit']} | {row['status']} |"
        )
    lines.extend(
        [
            "",
            "## 零值与证据边界",
            "",
            (
                f"本结果中有 {len(profile['zero_signals'])} 项精确零值。"
                "零值只是一项精确的数值信号，不能证明不存在排放或不存在环境影响。"
            ),
            (
                "Provider contribution cannot be reconstructed：公开证据没有暴露 Provider "
                "数值权重，因此不能重建贡献顺序，也不能作来源归因。"
            ),
            "本报告不进行无权威来源的归一化、加权、端点聚合或因果推断。",
            "",
            "## 可支持的结论",
            "",
            "- 25 项 TianGong 官方方法、版本、数值和单位已按来源逐项保留。",
            "- 四组预先冻结的同单位关系均在既定容差内通过。",
            "- 任何更强的贡献、因果或跨单位比较结论都超出当前公开证据。",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def assemble_native_lcia_interpretation_package(project: object, context: object) -> dict:
    """Assemble the exact machine package and its bounded Chinese report."""

    inputs = _inputs(project, context)
    existing = _projected(
        project, "native_lcia_interpretation_package", inputs["input_version"]
    )
    if existing is not None:
        _projected(project, "native_lcia_interpretation_report", inputs["input_version"])
        return existing
    profile_state = _projected(
        project, "native_lcia_interpretation_profile", inputs["input_version"]
    )
    if profile_state is None:
        raise RuntimeError("native LCIA interpretation profile is not compiled")
    profile = _artifact_json(
        project, profile_state, "native_lcia_interpretation_profile"
    )
    profile_errors = validate_native_lcia_profile(profile)
    if profile_errors:
        raise RuntimeError("native LCIA interpretation profile drifted: " + "; ".join(profile_errors))
    functional_unit = _validate_functional_unit(
        inputs["functional_unit"], profile["source_identity"]["process_exact_ref"]
    )
    package = {
        "contract": PACKAGE_CONTRACT,
        "status": (
            "accepted_with_explicit_functional_unit_gap"
            if functional_unit["status"] == "explicit_human_readable_unit_gap"
            else "decision_grade_package_assembled"
        ),
        "work_item_id": inputs["context"]["work_item_id"],
        "work_item_content_sha256": inputs["context"]["work_item_content_sha256"],
        "input_version": inputs["input_version"],
        "authority": {
            "policy": "TianGong official identity, version, method, unit, and result remain first authority",
            "authority_receipt_ref": inputs["context"]["authority_receipt_ref"],
            "source_ref": inputs["refs"]["native_lcia_bound_result"],
            "interpretation_contract_ref": inputs["refs"][
                "native_lcia_interpretation_contract"
            ],
        },
        "functional_unit": functional_unit,
        "interpretation_profile": profile,
        "claim_ceiling": profile["claim_policy"],
        "forbidden_effect_counts": {
            "credential_access": 0,
            "payment": 0,
            "contract_acceptance": 0,
            "publication": 0,
            "external_write": 0,
            "external_message": 0,
            "irreversible": 0,
        },
    }
    root = f"artifacts/native-lcia-interpretation/{inputs['input_version'][:20]}"
    package_artifact = _register(
        project,
        relative=f"{root}/package.json",
        role="native_lcia_interpretation_package",
        content=_pretty_bytes(package),
        input_version=inputs["input_version"],
    )
    report_artifact = _register(
        project,
        relative=f"{root}/report.zh-CN.md",
        role="native_lcia_interpretation_report",
        content=_render_report(package),
        input_version=inputs["input_version"],
    )
    package_state = {
        "status": "native_lcia_interpretation_package_assembled",
        "validation_status": "accepted",
        "bounded_outcome_status": package["status"],
        "input_version": inputs["input_version"],
        "work_item_id": inputs["context"]["work_item_id"],
        "indicator_count": len(profile["indicators"]),
        "relation_check_count": len(profile["relation_checks"]),
        "package_path": package_artifact["artifact_path"],
        "package_sha256": package_artifact["artifact_sha256"],
        "report_path": report_artifact["artifact_path"],
        "report_sha256": report_artifact["artifact_sha256"],
        **package_artifact,
    }
    report_state = {
        "status": "native_lcia_interpretation_report_rendered",
        "input_version": inputs["input_version"],
        "work_item_id": inputs["context"]["work_item_id"],
        "report_path": report_artifact["artifact_path"],
        "report_sha256": report_artifact["artifact_sha256"],
        **report_artifact,
    }
    project.record(
        "research.native_lcia_interpretation.package.assembled",
        {"package": package_state, "report": report_state},
        actor="controller",
        idempotency_key=f"native-lcia:{inputs['input_version']}:package:assembled",
    )
    project.materialize_views()
    return package_state


def accept_native_lcia_interpretation_package_read_only(
    project: object, context: object
) -> dict:
    """Re-open and independently validate one Project-local interpretation package."""

    inputs = _inputs(project, context)
    existing = _projected(
        project, "native_lcia_interpretation_acceptance", inputs["input_version"]
    )
    if existing is not None:
        return existing
    package_state = _projected(
        project, "native_lcia_interpretation_package", inputs["input_version"]
    )
    report_state = _projected(
        project, "native_lcia_interpretation_report", inputs["input_version"]
    )
    if package_state is None or report_state is None:
        raise RuntimeError("native LCIA interpretation package is not assembled")
    package = _artifact_json(
        project, package_state, "native_lcia_interpretation_package"
    )
    profile = package.get("interpretation_profile")
    if not isinstance(profile, Mapping) or validate_native_lcia_profile(profile):
        raise RuntimeError("native LCIA interpretation package profile drifted")
    _, report_content = _registered_ref(
        project,
        {
            "role": "native_lcia_interpretation_report",
            "path": report_state["artifact_path"],
            "sha256": report_state["artifact_sha256"],
            "bytes": report_state["artifact_bytes"],
        },
        "native_lcia_interpretation_report",
        expected_role="native_lcia_interpretation_report",
    )
    if report_content != _render_report(package):
        raise RuntimeError("native LCIA interpretation report and package disagree")
    if package.get("input_version") != inputs["input_version"]:
        raise RuntimeError("native LCIA interpretation package context drifted")
    if package.get("authority", {}).get("source_ref") != inputs["refs"][
        "native_lcia_bound_result"
    ]:
        raise RuntimeError("native LCIA interpretation package authority drifted")
    if any(package.get("forbidden_effect_counts", {}).values()):
        raise RuntimeError("native LCIA interpretation package has a forbidden effect")
    acceptance = {
        "contract": ACCEPTANCE_CONTRACT,
        "status": "accepted",
        "input_version": inputs["input_version"],
        "work_item_id": inputs["context"]["work_item_id"],
        "source_package_unchanged": True,
        "report_package_agreement": True,
        "indicator_count": len(profile["indicators"]),
        "relation_check_count": len(profile["relation_checks"]),
        "claim_ceiling_respected": True,
        "provider_numeric_contribution_reconstructed": False,
        "forbidden_effect_counts": dict(package["forbidden_effect_counts"]),
        "package_ref": {
            "role": "native_lcia_interpretation_package",
            "path": package_state["artifact_path"],
            "sha256": package_state["artifact_sha256"],
            "bytes": package_state["artifact_bytes"],
        },
        "report_ref": {
            "role": "native_lcia_interpretation_report",
            "path": report_state["artifact_path"],
            "sha256": report_state["artifact_sha256"],
            "bytes": report_state["artifact_bytes"],
        },
    }
    root = f"artifacts/native-lcia-interpretation/{inputs['input_version'][:20]}"
    artifact = _register(
        project,
        relative=f"{root}/acceptance.json",
        role="native_lcia_interpretation_acceptance",
        content=_pretty_bytes(acceptance),
        input_version=inputs["input_version"],
    )
    state = {
        "status": "native_lcia_interpretation_package_independently_accepted",
        "independent_acceptance_status": "accepted",
        "independent_acceptance_source_package_unchanged": True,
        "input_version": inputs["input_version"],
        "work_item_id": inputs["context"]["work_item_id"],
        "acceptance_path": artifact["artifact_path"],
        "acceptance_sha256": artifact["artifact_sha256"],
        **artifact,
    }
    project.record(
        "research.native_lcia_interpretation.package.independently_accepted",
        {"acceptance": state},
        actor="controller",
        idempotency_key=f"native-lcia:{inputs['input_version']}:package:accepted",
    )
    project.materialize_views()
    return state


def project_native_lcia_interpretation_events(events: object) -> dict:
    """Deterministically project accepted native-LCIA workflow events."""

    projection: dict[str, dict] = {}
    for event in events:
        event_type = event.get("event_type")
        if event_type not in EVENT_TYPES:
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("native LCIA interpretation event payload is invalid")
        if event_type == "research.native_lcia_interpretation.profile.compiled":
            rows = {"native_lcia_interpretation_profile": payload.get("profile")}
        elif event_type == "research.native_lcia_interpretation.package.assembled":
            if "native_lcia_interpretation_profile" not in projection:
                raise ValueError("native LCIA interpretation package precedes its profile")
            rows = {
                "native_lcia_interpretation_package": payload.get("package"),
                "native_lcia_interpretation_report": payload.get("report"),
            }
        else:
            if "native_lcia_interpretation_package" not in projection:
                raise ValueError("native LCIA interpretation acceptance precedes its package")
            rows = {"native_lcia_interpretation_acceptance": payload.get("acceptance")}
        for key, value in rows.items():
            if not isinstance(value, Mapping):
                raise ValueError(f"native LCIA interpretation projection is invalid: {key}")
            previous = projection.get(key)
            if previous is not None and previous != value:
                raise ValueError(f"native LCIA interpretation history conflicts: {key}")
            projection[key] = dict(value)
    return projection
