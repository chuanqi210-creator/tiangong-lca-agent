#!/usr/bin/env python3
"""Secret-safe, non-scientific structured probes for both adopted model routes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import stat
import sys
import tempfile
from pathlib import Path
from typing import Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PROVIDER_CATALOG = (
    ROOT / "lca_research_kernel/contracts/model-provider-catalog.json"
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _catalog(value: Optional[Mapping[str, object]] = None) -> dict:
    if value is None:
        loaded = json.loads(PROVIDER_CATALOG.read_text(encoding="utf-8"))
    else:
        loaded = dict(value)
    if (
        loaded.get("contract")
        != "lca_research_kernel.model_provider_catalog/v1"
        or not isinstance(loaded.get("providers"), list)
        or not isinstance(loaded.get("current_validation_gate"), list)
    ):
        raise ValueError("model provider catalog is invalid")
    return loaded


def selected_validation_rows(
    provider_catalog: Optional[Mapping[str, object]] = None,
    *,
    provider_ids: Optional[set[str]] = None,
) -> list[dict]:
    catalog = _catalog(provider_catalog)
    providers = {
        row.get("provider_id"): row
        for row in catalog["providers"]
        if isinstance(row, Mapping)
    }
    gate = list(catalog["current_validation_gate"])
    selected = set(gate) if provider_ids is None else set(provider_ids)
    if not selected or not selected.issubset(set(gate)):
        raise ValueError("selected provider subset is outside the validation gate")
    result = []
    for provider_id in gate:
        if provider_id not in selected:
            continue
        row = providers.get(provider_id)
        if not isinstance(row, Mapping):
            raise ValueError("provider validation gate references an absent provider")
        result.append(dict(row))
    return result


def _validation_rows(
    provider_catalog: Optional[Mapping[str, object]] = None,
) -> list[dict]:
    """Compatibility alias for complete-gate validation."""

    return selected_validation_rows(provider_catalog)


def _provider_rows(
    provider_catalog: Optional[Mapping[str, object]] = None,
) -> list[dict]:
    catalog = _catalog(provider_catalog)
    return [
        dict(row) for row in catalog["providers"] if isinstance(row, Mapping)
    ]


def _allowed_configuration(
    provider_catalog: Optional[Mapping[str, object]] = None,
) -> set[str]:
    from lca_research_kernel.model_credentials import (
        credential_environment_name,
        model_environment_name,
    )

    result = {"D18_MODEL_ROUTE_ORDER"}
    for row in _provider_rows(provider_catalog):
        for name in (
            credential_environment_name(row["credential_binding"]),
            model_environment_name(row["model_binding"]),
        ):
            if name is not None:
                result.add(name)
    return result


def configuration_presence(
    environ: Mapping[str, str],
    provider_catalog: Optional[Mapping[str, object]] = None,
    *,
    provider_ids: Optional[set[str]] = None,
    credential_presence=None,
) -> dict[str, bool]:
    from lca_research_kernel.model_credentials import (
        credential_present,
        resolve_model_id,
    )

    if credential_presence is None:
        credential_presence = credential_present
    result = {}
    for row in selected_validation_rows(
        provider_catalog, provider_ids=provider_ids
    ):
        provider_id = str(row["provider_id"])
        result[f"{provider_id}_credential"] = bool(
            credential_presence(row["credential_binding"], environ)
        )
        try:
            resolve_model_id(row["model_binding"], environ)
        except ValueError:
            model_present = False
        else:
            model_present = True
        result[f"{provider_id}_model_id"] = model_present
    return result


def load_credential_environment(
    path: Path,
    provider_catalog: Optional[Mapping[str, object]] = None,
) -> dict[str, str]:
    path = Path(path).expanduser()
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise PermissionError(
            "model runtime configuration must be user-owned with mode 0600"
        )
    result = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        if not separator or key not in _allowed_configuration(provider_catalog):
            raise ValueError(
                f"model runtime configuration field is unsupported at line {number}"
            )
        parsed = shlex.split(value, posix=True)
        if len(parsed) != 1:
            raise ValueError(
                f"model runtime configuration value is invalid at line {number}"
            )
        result[key] = parsed[0]
    return result


def build_collaboration_checklist(
    presence: Mapping[str, bool],
    provider_catalog: Optional[Mapping[str, object]] = None,
    *,
    provider_ids: Optional[set[str]] = None,
) -> dict:
    from lca_research_kernel.model_credentials import (
        credential_environment_name,
        model_environment_name,
    )

    rows = selected_validation_rows(
        provider_catalog, provider_ids=provider_ids
    )
    provider_names = [str(row["provider_id"]) for row in rows]
    model_fields = [
        name
        for row in rows
        for name in [model_environment_name(row["model_binding"])]
        if name is not None and row["model_binding"].get("kind") == "environment"
    ]
    configuration_fields = sorted(
        name
        for row in rows
        for name in (
            credential_environment_name(row["credential_binding"]),
            (
                model_environment_name(row["model_binding"])
                if row["model_binding"].get("kind") == "environment"
                else None
            ),
        )
        if name is not None
    )
    keychain_services = sorted(
        str(row["credential_binding"]["service"])
        for row in rows
        if row["credential_binding"].get("kind") == "macos_keychain"
    )
    missing = sorted(key for key, available in presence.items() if not available)
    safe_setup = []
    if configuration_fields:
        safe_setup.extend(
            [
                "在本机创建 ~/.config/d18-lca-agent/model-runtime.env，并用本地编辑器写入；不要把值粘贴到对话、命令参数、日志或仓库。",
                "文件只允许包含 "
                + "、".join(configuration_fields)
                + "。",
                "运行 chmod 600 ~/.config/d18-lca-agent/model-runtime.env。",
            ]
        )
    if keychain_services:
        safe_setup.append(
            "Keychain 凭证只按服务名 "
            + "、".join(keychain_services)
            + " 在本机安全存储；探针只读取存在性和调用所需值，"
            "不把值写入环境文件、参数、日志或工件。"
        )
    value = {
        "contract": "lca_research_kernel.user_collaboration_checklist/v1",
        "status": "waiting_for_user",
        "reason": (
            "当前验证门槛中的模型供应商需要用户独占的凭证与精确模型配置。"
        ),
        "current_validation_gate": provider_names,
        "missing_configuration": missing,
        "minimum_scope": [
            "仅需调用一个无工具、无外部写入、严格结构化的短请求。",
            "API 凭证只需模型响应权限；不需要文件、发布或组织管理权限。",
            (
                "、".join(model_fields)
                + " 必须是账户实际可用的精确模型 ID。"
                if model_fields
                else "目录默认模型 ID 必须通过账户实际响应机械核验。"
            ),
        ],
        "safe_setup": safe_setup,
        "risk": (
            f"探针会向 {len(provider_names)} 个已选模型供应商各发送一个"
            "非科研、无工具的结构化短请求，可能产生极小 API 用量；"
            "不会发布、写入外部系统或触发 LCA 计算。"
        ),
        "mechanical_resume_command": (
            "runtime/model-python/.venv/bin/python "
            "scripts/probe_model_providers.py --root . --probe "
            "--credential-env-file ~/.config/d18-lca-agent/model-runtime.env"
        ),
        "verification": [
            "脚本先机械校验文件 owner/mode、精确路由和模型 ID。",
            "成功后只保存响应身份、实际模型 ID、哈希、token、耗时和脱敏状态；不保存凭证。",
        ],
        "external_effects_executed": 0,
        "next_route": "verify-real-model-probes-after-user-configuration",
    }
    value["checklist_sha256"] = _sha256(value)
    return value


def provider_receipt(
    candidate: Mapping[str, object],
    *,
    policy_sha256: str,
    source_lock_sha256: str,
    provider_catalog_sha256: str,
) -> dict:
    required = (
        "provider",
        "model",
        "provider_response_id",
        "request_sha256",
        "output",
        "usage",
        "duration_ms",
    )
    if any(candidate.get(key) is None for key in required):
        raise ValueError("provider candidate receipt metadata is incomplete")
    value = {
        "contract": "lca_research_kernel.model_provider_probe_receipt/v1",
        "status": "passed",
        "provider": candidate["provider"],
        "actual_model": candidate["model"],
        "provider_response_id": candidate["provider_response_id"],
        "request_sha256": candidate["request_sha256"],
        "output_sha256": _sha256(candidate["output"]),
        "usage": candidate["usage"],
        "duration_ms": candidate["duration_ms"],
        "cost": candidate.get("cost"),
        "policy_sha256": policy_sha256,
        "source_lock_sha256": source_lock_sha256,
        "provider_catalog_sha256": provider_catalog_sha256,
        "real_call": True,
        "structured_output_valid": True,
        "tool_requests": 0,
        "external_writes": 0,
        "scientific_result_created": False,
    }
    value["receipt_sha256"] = _sha256(value)
    return value


def run_probes(
    root: Path,
    environ: Mapping[str, str],
    output_root: Path,
    *,
    provider_ids: Optional[set[str]] = None,
) -> dict:
    sys.path.insert(0, str(root))
    from importlib import metadata

    from lca_research_kernel.execution_environment import (
        ProjectExecutionEnvironment,
    )
    from lca_research_kernel.model_runtime import (
        build_production_model_gateway,
        load_model_runtime_contract,
    )
    from lca_research_kernel.run_trace import RunTrace

    contract = load_model_runtime_contract(root)
    gateway = build_production_model_gateway(
        root,
        environ,
        execution_mode="verification_probe",
        provider_ids=provider_ids,
    )
    installed = {
        provider: metadata.version(row["distribution"])
        for provider, row in contract["sdk_sources"].items()
    }
    expected = {
        provider: row["version"]
        for provider, row in contract["sdk_sources"].items()
    }
    if installed != expected:
        raise RuntimeError("installed provider SDK versions differ from source lock")
    execution_policy = json.loads(
        (
            root / "lca_research_kernel/contracts/project-execution-policy.json"
        ).read_text(encoding="utf-8")
    )
    execution_environment = ProjectExecutionEnvironment.create(
        output_root / "sandbox",
        "d18-model-connectivity-probe",
        execution_policy,
    )
    gateway = gateway.with_execution_environment(execution_environment)
    schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["ok"]},
            "summary": {"type": "string"},
        },
        "required": ["status", "summary"],
        "additionalProperties": False,
    }
    receipts = {}
    for row in selected_validation_rows(
        contract["provider_catalog"], provider_ids=provider_ids
    ):
        route_id = str(row["route_id"])
        provider = str(row["provider_id"])
        request = {
            "contract": "lca_research_kernel.model_request/v1",
            "request_id": f"d18-runtime-probe-{provider}",
            "provider": route_id,
            "task": (
                "Return status 'ok' and a short statement that this is a "
                "non-scientific runtime connectivity probe."
            ),
            "context": {
                "contract": "lca_research_kernel.runtime_probe_context/v1",
                "untrusted_content": False,
                "scientific_question": False,
                "external_effects_allowed": [],
            },
            "output_schema": schema,
            "tools": [],
            "required_capabilities": ["structured_output"],
        }
        trace = RunTrace(
            output_root / f"{provider}-probe-run-trace.jsonl",
            run_id=f"d18-runtime-probe-{provider}",
        )
        candidate = gateway.reason(request, trace=trace)
        if candidate.get("provider") != provider or candidate.get("tool_requests"):
            raise ValueError("provider probe returned an invalid identity or tool request")
        receipt = provider_receipt(
            candidate,
            policy_sha256=contract["policy_sha256"],
            source_lock_sha256=contract["source_lock_sha256"],
            provider_catalog_sha256=contract["provider_catalog_sha256"],
        )
        destination = output_root / f"{provider}-structured-probe-receipt.json"
        _atomic_json(destination, receipt)
        receipts[provider] = receipt
    return receipts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--credential-env-file", type=Path)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--presence", action="store_true")
    parser.add_argument(
        "--provider",
        action="append",
        dest="providers",
        help="Probe one current-gate provider; repeat to select more.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("outputs/d18-production-runtime-core/final"),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    catalog = _catalog()
    provider_ids = set(args.providers) if args.providers else None
    selected_validation_rows(catalog, provider_ids=provider_ids)
    allowed = _allowed_configuration(catalog)
    environ = {key: os.environ[key] for key in allowed if key in os.environ}
    if args.credential_env_file is not None:
        environ.update(load_credential_environment(args.credential_env_file, catalog))
    presence = configuration_presence(
        environ, catalog, provider_ids=provider_ids
    )
    if args.presence:
        print(json.dumps(presence, sort_keys=True))
        return 0
    output_root = args.output_root
    if not output_root.is_absolute():
        output_root = root / output_root
    if not all(presence.values()):
        checklist = build_collaboration_checklist(
            presence, catalog, provider_ids=provider_ids
        )
        _atomic_json(
            output_root / "user-collaboration-checklist-v3.json",
            checklist,
        )
        print(
            json.dumps(
                {
                    "status": "waiting_for_user",
                    "missing_count": len(checklist["missing_configuration"]),
                    "next_route": checklist["next_route"],
                },
                sort_keys=True,
            )
        )
        return 2
    if not args.probe:
        print(json.dumps({"status": "configuration_present"}, sort_keys=True))
        return 0
    receipts = run_probes(
        root, environ, output_root, provider_ids=provider_ids
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "providers": sorted(receipts),
                "external_writes": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
