"""Thin existing-Project registration for a compiled benchmark portfolio."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import threading
from contextlib import contextmanager
import fcntl
from pathlib import Path
from typing import Dict, Iterator, List, Mapping, Optional, Protocol, Sequence

from .research_council_benchmark_portfolio import (
    INPUT_CONTRACT,
    RUNTIME_ROLE_PATHS,
    absolute_nofollow_path,
    compile_benchmark_portfolio,
    pretty_json_bytes,
)
from .research_council_positive_benchmark import _walk_security


JsonObject = Dict[str, object]
WORKFLOW_CONTRACT = (
    "lca_research_kernel.research_council_benchmark_portfolio_registration/v4"
)
MANIFEST_CONTRACT = (
    "lca_research_kernel.research_council_benchmark_portfolio_manifest/v4"
)
REPLAY_CONTRACT = (
    "lca_research_kernel.research_council_benchmark_portfolio_replay/v4"
)
_EVENT_TYPES = [
    "research.research_council.benchmark_assets_registered",
    "research.research_council.benchmark_discovery_captured",
    "research.research_council.benchmark_portfolio_decided",
]
LINEAGE_CONTRACT = (
    "lca_research_kernel.research_council_benchmark_portfolio_lineage/v4"
)
LINEAGE_CONTRACT_V3 = (
    "lca_research_kernel.research_council_benchmark_portfolio_lineage/v3"
)
LINEAGE_CONTRACT_V2 = (
    "lca_research_kernel.research_council_benchmark_portfolio_lineage/v2"
)
_VERSIONED_LINEAGE_CONTRACTS = {
    LINEAGE_CONTRACT_V2,
    LINEAGE_CONTRACT_V3,
    LINEAGE_CONTRACT,
}
_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKSPACE_THREAD_LOCKS: Dict[str, threading.RLock] = {}
_WORKSPACE_THREAD_LOCKS_GUARD = threading.Lock()
_LOCK_DIRECTORY = ".research-kernel-locks"
_LOCK_FILE = "benchmark-portfolio-registration.lock"
_ZERO_EFFECTS = {
    "remote_write_executed_count": 0,
    "shared_write_executed_count": 0,
    "calculation_trigger_executed_count": 0,
    "publication_executed_count": 0,
    "review_submission_executed_count": 0,
    "contact_executed_count": 0,
    "payment_executed_count": 0,
    "role_administration_executed_count": 0,
    "external_message_executed_count": 0,
    "unofficial_tiangong_correction_count": 0,
    "unsupported_strong_public_claim_count": 0,
    "secret_persistence_count": 0,
}
_RUNTIME_ROLE_PATHS_V3 = {
    role: path
    for role, path in RUNTIME_ROLE_PATHS.items()
    if role
    not in {
        "tiangong_authority_transition_verifier",
        "tiangong_capability_adoption_verifier",
    }
}


class ProjectPort(Protocol):
    workspace: Path

    def events(self) -> List[JsonObject]: ...

    def record(
        self,
        event_type: str,
        payload: JsonObject,
        *,
        actor: str = "ai",
        idempotency_key: Optional[str] = None,
    ) -> JsonObject: ...

    def materialize_views(self) -> JsonObject: ...


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _workspace_thread_lock(workspace: Path) -> threading.RLock:
    key = os.path.normcase(str(workspace))
    with _WORKSPACE_THREAD_LOCKS_GUARD:
        lock = _WORKSPACE_THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _WORKSPACE_THREAD_LOCKS[key] = lock
        return lock


@contextmanager
def _workspace_registration_transaction(project: ProjectPort) -> Iterator[Path]:
    """Serialize this workflow across Project instances, threads, and processes."""

    workspace = absolute_nofollow_path(
        Path(project.workspace),
        label="benchmark portfolio workspace",
        allow_missing_tail=False,
    )
    thread_lock = _workspace_thread_lock(workspace)
    with thread_lock:
        lock_directory = workspace / _LOCK_DIRECTORY
        try:
            os.mkdir(lock_directory, 0o700)
        except FileExistsError:
            directory_status = os.lstat(lock_directory)
            if (
                stat.S_ISLNK(directory_status.st_mode)
                or not stat.S_ISDIR(directory_status.st_mode)
                or stat.S_IMODE(directory_status.st_mode) != 0o700
            ):
                raise RuntimeError("benchmark portfolio lock directory is unsafe")
        else:
            os.chmod(lock_directory, 0o700)
        lock_path = lock_directory / _LOCK_FILE
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            lock_status = os.fstat(descriptor)
            if not stat.S_ISREG(lock_status.st_mode):
                raise RuntimeError("benchmark portfolio lock is not a regular file")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield workspace
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _descriptor(relative: Path, content: bytes) -> JsonObject:
    return {
        "path": relative.as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _read_input_manifest(path: Path) -> JsonObject:
    source = absolute_nofollow_path(
        Path(path), label="portfolio input manifest", allow_missing_tail=False
    )
    if not stat.S_ISREG(os.lstat(source).st_mode):
        raise ValueError("portfolio input manifest must be a real local file")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("portfolio input manifest must be UTF-8 JSON") from error
    if (
        not isinstance(value, dict)
        or value.get("contract") != INPUT_CONTRACT
        or not isinstance(value.get("sources"), dict)
    ):
        raise ValueError("portfolio input manifest contract is invalid")
    return value


def _authority_refs(value: object) -> List[Mapping[str, object]]:
    refs: List[Mapping[str, object]] = []
    if isinstance(value, Mapping):
        raw = value.get("source_refs")
        if isinstance(raw, Mapping):
            candidates = list(raw.values())
        elif isinstance(raw, list):
            candidates = raw
        else:
            candidates = []
        for candidate in candidates:
            if isinstance(candidate, Mapping) and candidate not in refs:
                refs.append(candidate)
        for child in value.values():
            refs.extend(ref for ref in _authority_refs(child) if ref not in refs)
    elif isinstance(value, list):
        for child in value:
            refs.extend(ref for ref in _authority_refs(child) if ref not in refs)
    return refs


def _source_tree(
    path: Path,
    manifest: Mapping[str, object],
    *,
    authority_root: Path = _REPO_ROOT,
) -> Dict[Path, bytes]:
    source = absolute_nofollow_path(
        Path(path), label="portfolio input manifest", allow_missing_tail=False
    )
    root = source.parent
    files = {Path("source-tree/input-manifest.json"): source.read_bytes()}
    parsed_sources = []
    for key, raw in sorted(manifest["sources"].items()):
        if not isinstance(raw, Mapping) or set(raw) != {"role", "path", "sha256", "bytes"}:
            raise ValueError(f"portfolio input source {key} is malformed")
        relative = Path(str(raw["path"]))
        if relative.is_absolute() or ".." in relative.parts or "\\" in str(raw["path"]):
            raise ValueError(f"portfolio input source {key} path is unsafe")
        source_path = absolute_nofollow_path(
            root / relative,
            label=f"portfolio input source {key}",
            allow_missing_tail=False,
        )
        if not stat.S_ISREG(os.lstat(source_path).st_mode):
            raise ValueError(f"portfolio input source {key} is not a regular file")
        content = source_path.read_bytes()
        if (
            raw["bytes"] != len(content)
            or raw["sha256"] != hashlib.sha256(content).hexdigest()
        ):
            raise ValueError(f"portfolio input source {key} drifted")
        files[Path("source-tree") / relative] = content
        if relative.suffix == ".json":
            try:
                parsed_sources.append(json.loads(content.decode("utf-8")))
            except (UnicodeError, json.JSONDecodeError) as error:
                raise ValueError(f"portfolio input source {key} is not UTF-8 JSON") from error
    authority = absolute_nofollow_path(
        authority_root, label="portfolio authority root", allow_missing_tail=False
    )
    for value in parsed_sources:
        for ref in _authority_refs(value):
            if set(ref) != {"role", "path", "sha256", "bytes"}:
                raise ValueError("portfolio authority source ref is malformed")
            relative = Path(str(ref["path"]))
            if relative.is_absolute() or ".." in relative.parts or "\\" in str(ref["path"]):
                raise ValueError("portfolio authority source ref path is unsafe")
            authority_path = absolute_nofollow_path(
                authority / relative,
                label="portfolio authority source ref",
                allow_missing_tail=False,
            )
            content = authority_path.read_bytes()
            if (
                ref["bytes"] != len(content)
                or ref["sha256"] != hashlib.sha256(content).hexdigest()
            ):
                raise ValueError("portfolio authority source ref drifted")
            files[Path("source-tree/authority-root") / relative] = content
    for role, relative_text in sorted(RUNTIME_ROLE_PATHS.items()):
        relative = Path(relative_text)
        runtime_path = absolute_nofollow_path(
            _REPO_ROOT / relative,
            label=f"portfolio runtime {role}",
            allow_missing_tail=False,
        )
        files[Path("runtime-tree") / relative] = runtime_path.read_bytes()
    return files


def _human_status(portfolio: Mapping[str, object]) -> bytes:
    no_positive = (
        portfolio["portfolio_status"]
        == "no_positive_benchmark_currently_available"
    )
    if no_positive:
        conclusion = (
            "在本次完整捕获的 TianGong 已发布 LifecycleModel 表面中，尚无案例簇同时"
            "通过全部十项不可补偿门槛。这是有边界、可复核的正常结论，不是长期阻塞。"
        )
        route = (
            "只有 TianGong 候选、版本、Provider/LifecycleModel 引用或权威表面内容发生"
            "变化时，才重新运行有界资格审查。"
        )
    else:
        conclusion = (
            "至少一个真实竞争路线簇通过全部 TianGong 权威、Provider、功能单位、边界、"
            "原生结果和只读重放门槛，已形成不可变冻结提案。"
        )
        route = "独立验收通过后，可另建 Research Council 评价 Goal；本阶段没有运行 A/B。"
    text = f"""# TianGong-first 双轨基准资产组合（待独立验收）

## 当前结论

{conclusion}

## 双轨资产

- 负向轨道保留 53 个已审计候选，分布为铜 6、污水 2、柴油/LNG 卡车 45；它们用于检验 Agent 是否会拒绝补造 Provider 或降低门槛。
- 更新监测轨道保存当前内容指纹；内容不变时不重复长推理。
- Freshwater 仅校验 Harness 能重新打开一个完整案例，结构上不能成为正式正向基准。
- 正向轨道从 TianGong state-100 LifecycleModel 表面出发，逐候选保存十项硬门槛状态和拒绝依据。

## 下一步

{route}

本工件是 Task 5 的终态候选交付。正式单 Controller、既有 Authority Receipt、历史事件、工具版本和结果均未改写；Council 未晋升。
"""
    return text.encode("utf-8")


def _lineage_record(
    portfolio: Mapping[str, object],
    supersedes: Optional[Mapping[str, object]],
) -> JsonObject:
    body: JsonObject = {
        "contract": LINEAGE_CONTRACT,
        "negative_assets_id": portfolio["negative_track"]["negative_assets_id"],
        "surface_capture_id": portfolio["positive_discovery"]["surface_capture_id"],
        "portfolio_content_sha256": portfolio["portfolio_content_sha256"],
        "supersedes_portfolio_id": (
            supersedes.get("portfolio_id") if supersedes is not None else None
        ),
        "supersedes_manifest_sha256": (
            supersedes.get("manifest_sha256") if supersedes is not None else None
        ),
    }
    return {
        **body,
        "portfolio_lineage_id": hashlib.sha256(_canonical_bytes(body)).hexdigest()[:16],
    }


def _bundle_files(
    portfolio: Mapping[str, object],
    *,
    input_manifest_path: Path,
    input_manifest: Mapping[str, object],
    supersedes: Optional[Mapping[str, object]],
    authority_root: Path = _REPO_ROOT,
) -> Dict[Path, bytes]:
    files = _source_tree(
        input_manifest_path, input_manifest, authority_root=authority_root
    )
    lineage = _lineage_record(portfolio, supersedes)
    files[Path("portfolio.json")] = pretty_json_bytes(portfolio)
    freeze = portfolio.get("freeze_proposal")
    if isinstance(freeze, Mapping):
        files[Path("freeze-proposal.json")] = pretty_json_bytes(freeze)
    validation = {
        "contract": "lca_research_kernel.research_council_benchmark_portfolio_validation/v1",
        "portfolio_id": portfolio["portfolio_id"],
        "terminal_predicate_derived": True,
        "negative_assets_valid": True,
        "both_provider_audits_bound": True,
        "freshwater_canary_valid_nonpromotable": True,
        "surface_exhausted": True,
        "positive_qualification_completed": True,
        "all_prohibited_effects_zero": True,
        "council_ab_executed": False,
        "council_promoted": False,
        "status": "passed",
        "next_route": "accept-benchmark-portfolio-read-only",
    }
    files[Path("validation.json")] = pretty_json_bytes(validation)
    machine = {
        "contract": "lca_research_kernel.research_council_benchmark_portfolio_machine_context/v1",
        "portfolio_id": portfolio["portfolio_id"],
        "portfolio_status": portfolio["portfolio_status"],
        "council_disposition": portfolio["council_disposition"],
        "freeze_proposal_id": (
            portfolio["freeze_proposal"]["freeze_proposal_id"]
            if isinstance(portfolio.get("freeze_proposal"), Mapping)
            else None
        ),
        "negative_candidate_count": portfolio["negative_track"]["candidate_count"],
        "surface_capture_id": portfolio["positive_discovery"]["surface_capture_id"],
        "positive_qualification_id": portfolio["positive_discovery"]["qualification_id"],
        "admitted_cluster_count": portfolio["positive_discovery"]["admitted_cluster_count"],
        "terminal_next_route_after_acceptance": portfolio["next_route"],
        "next_route": "accept-benchmark-portfolio-read-only",
    }
    files[Path("machine-context.json")] = pretty_json_bytes(machine)
    files[Path("human-status.zh-CN.md")] = _human_status(portfolio)
    replay = {
        "contract": REPLAY_CONTRACT,
        "portfolio_id": portfolio["portfolio_id"],
        "portfolio_content_sha256": portfolio["portfolio_content_sha256"],
        "source_manifest_sha256": portfolio["source_manifest"]["sha256"],
        "replay_entry": "source-tree/input-manifest.json",
        "expected_result": "byte_identical_bundle_and_unchanged_event_count",
        "deterministic_replay_ready": True,
        "network_required": False,
        "credential_required": False,
        "effects": dict(_ZERO_EFFECTS),
        "next_route": "accept-benchmark-portfolio-read-only",
    }
    files[Path("replay.json")] = pretty_json_bytes(replay)
    assets_registration = {
        "contract": "lca_research_kernel.research_council.benchmark_assets_registration/v4",
        "portfolio_id": portfolio["portfolio_id"],
        "portfolio_lineage": lineage,
        "negative_track": portfolio["negative_track"],
        "update_monitor": portfolio["update_monitor"],
        "harness_canary": portfolio["harness_canary"],
        "surface_capture_id": portfolio["positive_discovery"]["surface_capture_id"],
    }
    discovery_registration = {
        "contract": "lca_research_kernel.research_council.benchmark_discovery_registration/v4",
        "portfolio_id": portfolio["portfolio_id"],
        "portfolio_lineage": lineage,
        "positive_discovery": portfolio["positive_discovery"],
        "admitted_clusters": portfolio["admitted_clusters"],
        "negative_assets_id": portfolio["negative_track"]["negative_assets_id"],
    }
    files[Path("assets-registration.json")] = pretty_json_bytes(assets_registration)
    files[Path("discovery-registration.json")] = pretty_json_bytes(
        discovery_registration
    )
    return files


def _manifest(
    portfolio: Mapping[str, object],
    files: Mapping[Path, bytes],
    *,
    supersedes: Optional[Mapping[str, object]],
) -> JsonObject:
    deliverables = {
        relative.as_posix(): _descriptor(relative, content)
        for relative, content in sorted(files.items(), key=lambda item: item[0].as_posix())
    }
    authority_refs = []
    for source_name in (
        "negative_assets",
        "freshwater_canary",
        "positive_qualification",
    ):
        source_path = Path("source-tree/inputs") / f"{source_name}.json"
        value = json.loads(files[source_path].decode("utf-8"))
        refs = value.get("source_refs")
        raw_refs = refs.values() if isinstance(refs, Mapping) else refs
        if not isinstance(raw_refs, (list, tuple)) and not hasattr(raw_refs, "__iter__"):
            raise ValueError(f"{source_name} does not expose source refs")
        for ref in raw_refs:
            if ref not in authority_refs:
                authority_refs.append(ref)
    authority_refs.sort(key=lambda value: (str(value.get("path")), str(value.get("role"))))
    lineage_body = _lineage_record(portfolio, supersedes)
    runtime_closure = []
    for raw in portfolio["runtime_closure"]:
        source_path = Path(str(raw["path"]))
        bundled_path = Path("runtime-tree") / source_path
        content = files[bundled_path]
        runtime_closure.append(
            {
                "role": raw["role"],
                "source_path": source_path.as_posix(),
                **_descriptor(bundled_path, content),
            }
        )
    workflow_runtime = next(
        copy
        for copy in runtime_closure
        if copy["role"] == "benchmark_portfolio_workflow"
    )
    body: JsonObject = {
        "contract": MANIFEST_CONTRACT,
        "workflow_runtime": workflow_runtime,
        "runtime_closure": runtime_closure,
        "portfolio_id": portfolio["portfolio_id"],
        "portfolio_content_sha256": portfolio["portfolio_content_sha256"],
        "portfolio_status": portfolio["portfolio_status"],
        "council_disposition": portfolio["council_disposition"],
        "freeze_proposal_id": (
            portfolio["freeze_proposal"]["freeze_proposal_id"]
            if isinstance(portfolio.get("freeze_proposal"), Mapping)
            else None
        ),
        "portfolio_lineage": lineage_body,
        "deliverables": deliverables,
        "authority_source_refs": authority_refs,
        "file_count": len(deliverables),
        "all_prohibited_effects_zero": True,
        "next_route": "accept-benchmark-portfolio-read-only",
    }
    digest = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    return {**body, "manifest_content_sha256": digest, "manifest_id": digest[:16]}


def _event_contracts(
    portfolio: Mapping[str, object],
    *,
    bundle_root: Path,
    files: Mapping[Path, bytes],
    manifest: Mapping[str, object],
) -> List[tuple[str, JsonObject, str]]:
    lineage_record = manifest["portfolio_lineage"]
    lineage = lineage_record["portfolio_lineage_id"]
    shared = {
        "portfolio_lineage_contract": LINEAGE_CONTRACT,
        "portfolio_lineage_id": lineage,
        "supersedes_portfolio_id": lineage_record["supersedes_portfolio_id"],
        "supersedes_manifest_sha256": lineage_record[
            "supersedes_manifest_sha256"
        ],
    }

    def artifact(relative: Path, content: bytes) -> JsonObject:
        full = bundle_root / relative
        return _descriptor(full, content)

    assets = artifact(
        Path("assets-registration.json"), files[Path("assets-registration.json")]
    )
    discovery = artifact(
        Path("discovery-registration.json"),
        files[Path("discovery-registration.json")],
    )
    manifest_bytes = pretty_json_bytes(manifest)
    decided = artifact(Path("manifest.json"), manifest_bytes)
    payloads = [
        {
            **shared,
            "portfolio_id": portfolio["portfolio_id"],
            "negative_assets_id": portfolio["negative_track"]["negative_assets_id"],
            "monitor_id": portfolio["update_monitor"]["monitor_id"],
            "candidate_count": 53,
            "artifact": assets,
            "effects": dict(_ZERO_EFFECTS),
            "next_route": "register-benchmark-discovery",
        },
        {
            **shared,
            "portfolio_id": portfolio["portfolio_id"],
            "surface_capture_id": portfolio["positive_discovery"]["surface_capture_id"],
            "qualification_id": portfolio["positive_discovery"]["qualification_id"],
            "positive_discovery_status": portfolio["positive_discovery"]["positive_discovery_status"],
            "artifact": discovery,
            "effects": dict(_ZERO_EFFECTS),
            "next_route": "decide-benchmark-portfolio",
        },
        {
            **shared,
            "portfolio_id": portfolio["portfolio_id"],
            "portfolio_status": portfolio["portfolio_status"],
            "council_disposition": portfolio["council_disposition"],
            "freeze_proposal_id": (
                portfolio["freeze_proposal"]["freeze_proposal_id"]
                if isinstance(portfolio.get("freeze_proposal"), Mapping)
                else None
            ),
            "artifact": decided,
            "effects": dict(_ZERO_EFFECTS),
            "next_route": "accept-benchmark-portfolio-read-only",
        },
    ]
    result = []
    for event_type, payload in zip(_EVENT_TYPES, payloads):
        key = (
            f"benchmark-portfolio-v4:{event_type.rsplit('.', 1)[-1]}:"
            f"{portfolio['portfolio_id']}:{lineage}"
        )
        result.append((event_type, payload, key))
    return result


def _preflight_events(
    project: ProjectPort,
    expected: Sequence[tuple[str, JsonObject, str]],
) -> None:
    current = project.events()
    lineage = expected[0][1]["portfolio_lineage_id"]
    observed = []
    for event in current:
        event_type = event.get("event_type")
        payload = event.get("payload")
        if event_type not in _EVENT_TYPES or not isinstance(payload, Mapping):
            continue
        if payload.get("portfolio_lineage_id") != lineage:
            continue
        observed.append(event_type)
        match = next(item for item in expected if item[0] == event_type)
        if (
            event.get("actor") != "controller"
            or event.get("idempotency_key") != match[2]
            or dict(payload) != match[1]
        ):
            raise ValueError(
                "benchmark portfolio logical identity already has a different successor"
            )
    if observed != _EVENT_TYPES[: len(observed)]:
        raise ValueError("benchmark portfolio event prefix is not append-only and ordered")


def _secure_write_bundle(
    project: ProjectPort,
    relative_root: Path,
    files: Mapping[Path, bytes],
) -> bool:
    workspace = absolute_nofollow_path(
        Path(project.workspace),
        label="benchmark portfolio workspace",
        allow_missing_tail=False,
    )
    target = workspace / relative_root

    def validate_existing() -> None:
        expected_files = {relative.as_posix() for relative in files}
        expected_dirs = {"."}
        for relative in files:
            expected_dirs.update(
                parent.as_posix()
                for parent in relative.parents
                if parent != Path(".")
            )
        actual_files = set()
        actual_dirs = {"."}
        try:
            root_status = os.lstat(target)
        except FileNotFoundError as error:
            raise RuntimeError("content-addressed benchmark portfolio bundle vanished") from error
        if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
            raise RuntimeError("benchmark portfolio output path is not a safe directory")
        if stat.S_IMODE(root_status.st_mode) != 0o700:
            raise RuntimeError("benchmark portfolio bundle directory mode drifted")
        for path in target.rglob("*"):
            status = os.lstat(path)
            relative_text = path.relative_to(target).as_posix()
            if stat.S_ISLNK(status.st_mode):
                raise RuntimeError(
                    "content-addressed benchmark portfolio bundle contains a symlink"
                )
            if stat.S_ISDIR(status.st_mode):
                actual_dirs.add(relative_text)
                if stat.S_IMODE(status.st_mode) != 0o700:
                    raise RuntimeError("benchmark portfolio bundle directory mode drifted")
            elif stat.S_ISREG(status.st_mode):
                actual_files.add(relative_text)
                if stat.S_IMODE(status.st_mode) != 0o600:
                    raise RuntimeError("benchmark portfolio bundle file mode drifted")
            else:
                raise RuntimeError("benchmark portfolio bundle contains a special file")
        if actual_files != expected_files or actual_dirs != expected_dirs:
            raise RuntimeError("content-addressed benchmark portfolio bundle drifted")
        for relative, content in files.items():
            if (target / relative).read_bytes() != content:
                raise RuntimeError("content-addressed benchmark portfolio file drifted")

    cursor = workspace
    for part in relative_root.parent.parts:
        cursor = cursor / part
        try:
            status = os.lstat(cursor)
        except FileNotFoundError:
            try:
                os.mkdir(cursor, 0o700)
            except FileExistsError:
                status = os.lstat(cursor)
            else:
                status = os.lstat(cursor)
                os.chmod(cursor, 0o700)
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
            raise RuntimeError("benchmark portfolio output parent is unsafe")
    try:
        target.mkdir(mode=0o700, parents=False, exist_ok=False)
    except FileExistsError:
        validate_existing()
        return False
    os.chmod(target, 0o700)
    try:
        created_dirs = {Path(".")}
        for relative, content in sorted(files.items(), key=lambda item: item[0].as_posix()):
            for parent in reversed(relative.parents):
                if parent == Path(".") or parent in created_dirs:
                    continue
                directory = target / parent
                directory.mkdir(mode=0o700, parents=False, exist_ok=False)
                os.chmod(directory, 0o700)
                created_dirs.add(parent)
            destination = target / relative
            descriptor = os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
        validate_existing()
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return True


def _strict_registered_json(
    project: ProjectPort, descriptor: object, *, label: str
) -> tuple[Path, JsonObject]:
    if not isinstance(descriptor, Mapping) or set(descriptor) != {
        "path",
        "sha256",
        "bytes",
    }:
        raise ValueError(f"{label} descriptor is malformed")
    relative = Path(str(descriptor["path"]))
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "\\" in str(descriptor["path"])
        or relative.parts[:1] != ("research-council-benchmark",)
    ):
        raise ValueError(f"{label} descriptor leaves its namespace")
    path = absolute_nofollow_path(
        Path(project.workspace) / relative,
        label=label,
        allow_missing_tail=False,
    )
    status = os.lstat(path)
    if not stat.S_ISREG(status.st_mode) or stat.S_IMODE(status.st_mode) != 0o600:
        raise ValueError(f"{label} must be a mode-0600 regular file")
    content = path.read_bytes()
    if (
        isinstance(descriptor["bytes"], bool)
        or descriptor["bytes"] != len(content)
        or descriptor["sha256"] != hashlib.sha256(content).hexdigest()
    ):
        raise ValueError(f"{label} descriptor drifted")
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain an object")
    _walk_security(value)
    return path, value


def _validate_bundle_tree(
    bundle: Path,
    manifest: Mapping[str, object],
    *,
    lineage_version: int,
) -> None:
    root_status = os.lstat(bundle)
    if not stat.S_ISDIR(root_status.st_mode) or stat.S_IMODE(root_status.st_mode) != 0o700:
        raise ValueError("benchmark portfolio bundle root mode is unsafe")
    deliverables = manifest.get("deliverables")
    if not isinstance(deliverables, Mapping):
        raise ValueError("benchmark portfolio deliverables are malformed")
    expected_files = {"manifest.json", *deliverables.keys()}
    expected_dirs = {"."}
    for relative_text in deliverables:
        relative = Path(str(relative_text))
        if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative_text):
            raise ValueError("benchmark portfolio deliverable path is unsafe")
        expected_dirs.update(
            parent.as_posix()
            for parent in relative.parents
            if parent != Path(".")
        )
    actual_files = set()
    actual_dirs = {"."}
    for path in bundle.rglob("*"):
        status = os.lstat(path)
        relative_text = path.relative_to(bundle).as_posix()
        if stat.S_ISLNK(status.st_mode):
            raise ValueError("benchmark portfolio bundle contains a symlink")
        if stat.S_ISDIR(status.st_mode):
            actual_dirs.add(relative_text)
            if stat.S_IMODE(status.st_mode) != 0o700:
                raise ValueError("benchmark portfolio directory mode is unsafe")
        elif stat.S_ISREG(status.st_mode):
            actual_files.add(relative_text)
            if stat.S_IMODE(status.st_mode) != 0o600:
                raise ValueError("benchmark portfolio file mode is unsafe")
        else:
            raise ValueError("benchmark portfolio bundle contains a special file")
    if actual_files != expected_files or actual_dirs != expected_dirs:
        raise ValueError("benchmark portfolio bundle contains missing or hidden entries")
    for relative_text, raw in deliverables.items():
        relative = Path(str(relative_text))
        if not isinstance(raw, Mapping) or set(raw) != {"path", "sha256", "bytes"}:
            raise ValueError("benchmark portfolio deliverable descriptor is malformed")
        if raw["path"] != relative.as_posix():
            raise ValueError("benchmark portfolio deliverable descriptor path drifted")
        content = (bundle / relative).read_bytes()
        if raw["bytes"] != len(content) or raw["sha256"] != hashlib.sha256(content).hexdigest():
            raise ValueError("benchmark portfolio deliverable descriptor drifted")
    if lineage_version >= 2:
        expected_runtime_paths = (
            RUNTIME_ROLE_PATHS if lineage_version >= 4 else _RUNTIME_ROLE_PATHS_V3
        )
        closure = manifest.get("runtime_closure")
        if not isinstance(closure, list) or {
            row.get("role") for row in closure if isinstance(row, Mapping)
        } != set(expected_runtime_paths):
            raise ValueError("benchmark portfolio runtime closure is incomplete")
        for row in closure:
            if not isinstance(row, Mapping) or set(row) != {
                "role",
                "source_path",
                "path",
                "sha256",
                "bytes",
            }:
                raise ValueError("benchmark portfolio runtime descriptor is malformed")
            expected_source = expected_runtime_paths.get(str(row["role"]))
            expected_path = f"runtime-tree/{expected_source}"
            if row["source_path"] != expected_source or row["path"] != expected_path:
                raise ValueError("benchmark portfolio runtime path drifted")
            if manifest["deliverables"].get(expected_path) != {
                "path": row["path"],
                "sha256": row["sha256"],
                "bytes": row["bytes"],
            }:
                raise ValueError("runtime closure is not bound as a deliverable")


def _semantic_portfolio(value: Mapping[str, object]) -> JsonObject:
    return {
        key: child
        for key, child in value.items()
        if key
        not in {
            "compiler_runtime",
            "runtime_closure",
            "portfolio_content_sha256",
            "portfolio_id",
        }
    }


def validate_benchmark_portfolio_event_artifact(
    project: ProjectPort,
    event_type: str,
    payload: Mapping[str, object],
    stages: Dict[str, JsonObject],
) -> tuple[JsonObject, JsonObject, str]:
    """Strictly replay one portfolio event; superseded versions stay immutable."""

    _walk_security(payload)
    lineage_contract = payload.get("portfolio_lineage_contract")
    lineage_version = (
        4
        if lineage_contract == LINEAGE_CONTRACT
        else 3
        if lineage_contract == LINEAGE_CONTRACT_V3
        else 2
        if lineage_contract == LINEAGE_CONTRACT_V2
        else 1
    )
    versioned = lineage_version >= 2
    lineage = str(payload.get("portfolio_lineage_id"))
    shared_versioned = {
        "portfolio_lineage_contract": lineage_contract,
        "portfolio_lineage_id": lineage,
        "supersedes_portfolio_id": payload.get("supersedes_portfolio_id"),
        "supersedes_manifest_sha256": payload.get("supersedes_manifest_sha256"),
    }
    path, output = _strict_registered_json(
        project, payload.get("artifact"), label=event_type
    )
    if event_type.endswith(".benchmark_assets_registered"):
        expected_contract = (
            f"lca_research_kernel.research_council.benchmark_assets_registration/v{lineage_version}"
            if versioned
            else "lca_research_kernel.research_council.benchmark_assets_registration/v1"
        )
        expected_keys = {
            "contract",
            "portfolio_id",
            "negative_track",
            "update_monitor",
            "harness_canary",
            "surface_capture_id",
        } | ({"portfolio_lineage"} if versioned else set())
        if set(output) != expected_keys or output.get("contract") != expected_contract:
            raise ValueError("benchmark asset registration artifact is malformed")
        negative = output.get("negative_track")
        monitor = output.get("update_monitor")
        canary = output.get("harness_canary")
        if (
            not isinstance(negative, Mapping)
            or set(negative) != {
                "negative_assets_id",
                "candidate_count",
                "cluster_counts",
                "provider_audit_ids",
                "positive_benchmark_member_count",
            }
            or negative.get("candidate_count") != 53
            or negative.get("positive_benchmark_member_count") != 0
            or not isinstance(monitor, Mapping)
            or set(monitor) != {
                "monitor_id",
                "monitor_fingerprint",
                "change_status",
            }
            or monitor.get("change_status") != "no_content_change"
            or not isinstance(canary, Mapping)
            or set(canary) != {
                "canary_id",
                "harness_valid",
                "formal_benchmark_eligible",
            }
            or canary.get("formal_benchmark_eligible") is not False
        ):
            raise ValueError("benchmark asset registration semantics drifted")
        if versioned:
            lineage_record = output.get("portfolio_lineage")
            if not isinstance(lineage_record, Mapping) or set(lineage_record) != {
                "contract",
                "negative_assets_id",
                "surface_capture_id",
                "portfolio_content_sha256",
                "supersedes_portfolio_id",
                "supersedes_manifest_sha256",
                "portfolio_lineage_id",
            }:
                raise ValueError("benchmark asset lineage is malformed")
            lineage_body = {
                key: value
                for key, value in lineage_record.items()
                if key != "portfolio_lineage_id"
            }
            if (
                lineage_record.get("contract") != lineage_contract
                or lineage_record.get("negative_assets_id")
                != negative.get("negative_assets_id")
                or lineage_record.get("surface_capture_id")
                != output.get("surface_capture_id")
                or lineage_record.get("supersedes_portfolio_id")
                != payload.get("supersedes_portfolio_id")
                or lineage_record.get("supersedes_manifest_sha256")
                != payload.get("supersedes_manifest_sha256")
                or lineage_record.get("portfolio_lineage_id") != lineage
                or output.get("portfolio_id")
                != str(lineage_record.get("portfolio_content_sha256"))[:16]
                or hashlib.sha256(_canonical_bytes(lineage_body)).hexdigest()[:16]
                != lineage
            ):
                raise ValueError("benchmark asset lineage is malformed")
        expected_payload = {
            **(shared_versioned if versioned else {"portfolio_lineage_id": lineage}),
            "portfolio_id": output.get("portfolio_id"),
            "negative_assets_id": negative.get("negative_assets_id"),
            "monitor_id": monitor.get("monitor_id"),
            "candidate_count": 53,
            "artifact": payload.get("artifact"),
            "effects": dict(_ZERO_EFFECTS),
            "next_route": "register-benchmark-discovery",
        }
        stages[lineage] = {
            "stage": "assets",
            "assets": output,
            "lineage_version": lineage_version,
        }
    elif event_type.endswith(".benchmark_discovery_captured"):
        expected_contract = (
            f"lca_research_kernel.research_council.benchmark_discovery_registration/v{lineage_version}"
            if versioned
            else "lca_research_kernel.research_council.benchmark_discovery_registration/v1"
        )
        expected_keys = {
            "contract",
            "portfolio_id",
            "positive_discovery",
            "admitted_clusters",
            "negative_assets_id",
        } | ({"portfolio_lineage"} if versioned else set())
        previous = stages.get(lineage)
        discovery = output.get("positive_discovery")
        if (
            previous is None
            or previous.get("stage") != "assets"
            or previous.get("lineage_version") != lineage_version
            or set(output) != expected_keys
            or output.get("contract") != expected_contract
            or not isinstance(discovery, Mapping)
            or set(discovery) != {
                "surface_capture_id",
                "surface_capture_content_sha256",
                "positive_discovery_status",
                "qualification_execution_status",
                "qualification_id",
                "candidate_count",
                "admitted_cluster_count",
            }
            or discovery.get("positive_discovery_status") != "exhausted"
            or discovery.get("qualification_execution_status") != "completed"
            or not isinstance(output.get("admitted_clusters"), list)
            or discovery.get("admitted_cluster_count")
            != len(output["admitted_clusters"])
            or output.get("portfolio_id")
            != previous["assets"].get("portfolio_id")
            or output.get("negative_assets_id")
            != previous["assets"]["negative_track"].get("negative_assets_id")
        ):
            raise ValueError("benchmark discovery registration artifact is malformed")
        if versioned and output.get("portfolio_lineage") != previous["assets"].get(
            "portfolio_lineage"
        ):
            raise ValueError("benchmark discovery lineage drifted")
        expected_payload = {
            **(shared_versioned if versioned else {"portfolio_lineage_id": lineage}),
            "portfolio_id": output.get("portfolio_id"),
            "surface_capture_id": discovery.get("surface_capture_id"),
            "qualification_id": discovery.get("qualification_id"),
            "positive_discovery_status": "exhausted",
            "artifact": payload.get("artifact"),
            "effects": dict(_ZERO_EFFECTS),
            "next_route": "decide-benchmark-portfolio",
        }
        stages[lineage] = {
            **previous,
            "stage": "discovery",
            "discovery": output,
        }
    elif event_type.endswith(".benchmark_portfolio_decided"):
        previous = stages.get(lineage)
        if (
            previous is None
            or previous.get("stage") != "discovery"
            or previous.get("lineage_version") != lineage_version
        ):
            raise ValueError("benchmark portfolio decision lacks exact discovery")
        manifest_keys = {
            "contract",
            "workflow_runtime",
            "portfolio_id",
            "portfolio_content_sha256",
            "portfolio_status",
            "council_disposition",
            "freeze_proposal_id",
            "portfolio_lineage",
            "deliverables",
            "authority_source_refs",
            "file_count",
            "all_prohibited_effects_zero",
            "next_route",
            "manifest_content_sha256",
            "manifest_id",
        } | ({"runtime_closure"} if versioned else set())
        expected_manifest_contract = (
            f"lca_research_kernel.research_council_benchmark_portfolio_manifest/v{lineage_version}"
            if versioned
            else "lca_research_kernel.research_council_benchmark_portfolio_manifest/v1"
        )
        body = {
            key: value
            for key, value in output.items()
            if key not in {"manifest_content_sha256", "manifest_id"}
        }
        digest = hashlib.sha256(_canonical_bytes(body)).hexdigest()
        if (
            set(output) != manifest_keys
            or output.get("contract") != expected_manifest_contract
            or output.get("manifest_content_sha256") != digest
            or output.get("manifest_id") != digest[:16]
            or output.get("file_count") != len(output.get("deliverables", {}))
            or output.get("all_prohibited_effects_zero") is not True
        ):
            raise ValueError("benchmark portfolio manifest is malformed")
        bundle = path.parent
        _validate_bundle_tree(bundle, output, lineage_version=lineage_version)
        authority_root = (
            bundle / "source-tree/authority-root" if versioned else _REPO_ROOT
        )
        input_path = bundle / "source-tree/input-manifest.json"
        recorded = json.loads((bundle / "portfolio.json").read_text(encoding="utf-8"))
        _walk_security(recorded)
        recorded_body = {
            key: value
            for key, value in recorded.items()
            if key not in {"portfolio_content_sha256", "portfolio_id"}
        }
        recorded_digest = hashlib.sha256(_canonical_bytes(recorded_body)).hexdigest()
        if (
            recorded.get("portfolio_content_sha256") != recorded_digest
            or recorded.get("portfolio_id") != recorded_digest[:16]
            or recorded.get("portfolio_id") != output.get("portfolio_id")
        ):
            raise ValueError("benchmark portfolio content identity drifted")
        if lineage_version == 4:
            regenerated = compile_benchmark_portfolio(
                input_manifest_path=input_path, authority_root=authority_root
            )
            supersedes = (
                {
                    "portfolio_id": payload.get("supersedes_portfolio_id"),
                    "manifest_sha256": payload.get("supersedes_manifest_sha256"),
                }
                if payload.get("supersedes_portfolio_id") is not None
                else None
            )
            if recorded != regenerated:
                raise ValueError("benchmark portfolio differs from pure semantic replay")
            input_manifest = _read_input_manifest(input_path)
            expected_files = _bundle_files(
                regenerated,
                input_manifest_path=input_path,
                input_manifest=input_manifest,
                supersedes=supersedes,
                authority_root=authority_root,
            )
            expected_manifest = _manifest(
                regenerated, expected_files, supersedes=supersedes
            )
            if output != expected_manifest:
                raise ValueError("benchmark portfolio manifest differs from replay")
            for relative, content in expected_files.items():
                if (bundle / relative).read_bytes() != content:
                    raise ValueError("benchmark portfolio deliverable differs from replay")
            if previous["assets"] != json.loads(expected_files[Path("assets-registration.json")]):
                raise ValueError("benchmark assets prefix differs from terminal replay")
            if previous["discovery"] != json.loads(expected_files[Path("discovery-registration.json")]):
                raise ValueError("benchmark discovery prefix differs from terminal replay")
        elif lineage_version == 3:
            regenerated = compile_benchmark_portfolio(
                input_manifest_path=input_path, authority_root=authority_root
            )
            if _semantic_portfolio(recorded) != _semantic_portfolio(regenerated):
                raise ValueError("superseded v3 portfolio science differs from replay")
            if previous["assets"] != json.loads(
                (bundle / "assets-registration.json").read_text(encoding="utf-8")
            ):
                raise ValueError("superseded benchmark assets prefix drifted")
            if previous["discovery"] != json.loads(
                (bundle / "discovery-registration.json").read_text(encoding="utf-8")
            ):
                raise ValueError("superseded benchmark discovery prefix drifted")
        elif lineage_version == 2:
            if previous["assets"] != json.loads(
                (bundle / "assets-registration.json").read_text(encoding="utf-8")
            ):
                raise ValueError("superseded benchmark assets prefix drifted")
            if previous["discovery"] != json.loads(
                (bundle / "discovery-registration.json").read_text(encoding="utf-8")
            ):
                raise ValueError("superseded benchmark discovery prefix drifted")
        else:
            regenerated = compile_benchmark_portfolio(
                input_manifest_path=input_path, authority_root=authority_root
            )
            if _semantic_portfolio(recorded) != _semantic_portfolio(regenerated):
                raise ValueError("legacy portfolio science differs from current replay")
        expected_payload = {
            **(shared_versioned if versioned else {"portfolio_lineage_id": lineage}),
            "portfolio_id": output.get("portfolio_id"),
            "portfolio_status": output.get("portfolio_status"),
            "council_disposition": output.get("council_disposition"),
            "freeze_proposal_id": output.get("freeze_proposal_id"),
            "artifact": payload.get("artifact"),
            "effects": dict(_ZERO_EFFECTS),
            "next_route": "accept-benchmark-portfolio-read-only",
        }
        stages[lineage] = {**previous, "stage": "decided", "manifest": output}
    else:
        raise ValueError("unsupported benchmark portfolio event")
    if dict(payload) != expected_payload:
        raise ValueError("benchmark portfolio event payload or lineage is invalid")
    return output, expected_payload, lineage


def _register_benchmark_portfolio_locked(
    project: ProjectPort, *, input_manifest_path: Path
) -> JsonObject:
    """Register while the caller holds the workspace transaction."""

    from .research_council_workflow import validate_research_council_event_stream

    validate_research_council_event_stream(project, project.events())
    input_manifest = _read_input_manifest(input_manifest_path)
    portfolio = compile_benchmark_portfolio(
        input_manifest_path=input_manifest_path
    )
    supersedes = None
    decided_events = [
        event
        for event in project.events()
        if event.get("event_type")
        == "research.research_council.benchmark_portfolio_decided"
        and isinstance(event.get("payload"), Mapping)
    ]
    if decided_events:
        latest_payload = decided_events[-1]["payload"]
        latest_contract = latest_payload.get("portfolio_lineage_contract")
        if latest_contract == LINEAGE_CONTRACT and latest_payload.get(
            "portfolio_id"
        ) == portfolio["portfolio_id"]:
            if latest_payload.get("supersedes_portfolio_id") is not None:
                supersedes = {
                    "portfolio_id": latest_payload["supersedes_portfolio_id"],
                    "manifest_sha256": latest_payload[
                        "supersedes_manifest_sha256"
                    ],
                }
        elif latest_contract in {LINEAGE_CONTRACT_V3, LINEAGE_CONTRACT_V2}:
            supersedes = {
                "portfolio_id": latest_payload.get("portfolio_id"),
                "manifest_sha256": latest_payload.get("artifact", {}).get("sha256"),
            }
        elif latest_contract is None:
            supersedes = {
                "portfolio_id": latest_payload.get("portfolio_id"),
                "manifest_sha256": latest_payload.get("artifact", {}).get("sha256"),
            }
        else:
            raise ValueError(
                "a distinct corrected benchmark terminal requires a future content-change contract"
            )
    bundle_root = (
        Path("research-council-benchmark")
        / "portfolio"
        / f"terminal-{portfolio['portfolio_id']}"
    )
    files = _bundle_files(
        portfolio,
        input_manifest_path=input_manifest_path,
        input_manifest=input_manifest,
        supersedes=supersedes,
    )
    manifest = _manifest(portfolio, files, supersedes=supersedes)
    files[Path("manifest.json")] = pretty_json_bytes(manifest)
    expected_events = _event_contracts(
        portfolio, bundle_root=bundle_root, files=files, manifest=manifest
    )
    _preflight_events(project, expected_events)
    created = _secure_write_bundle(project, bundle_root, files)
    events_before = len(project.events())
    try:
        hypothetical = list(project.events())
        existing_keys = {
            event.get("idempotency_key") for event in hypothetical
        }
        missing_events = [
            item for item in expected_events if item[2] not in existing_keys
        ]
        for offset, (event_type, payload, key) in enumerate(missing_events, start=1):
            hypothetical.append(
                {
                    "event_id": f"benchmark-portfolio-preflight-{offset}",
                    "sequence": events_before + offset,
                    "event_type": event_type,
                    "occurred_at": "preflight",
                    "actor": "controller",
                    "idempotency_key": key,
                    "payload": payload,
                }
            )
        validate_research_council_event_stream(project, hypothetical)
        for event_type, payload, key in expected_events:
            event = project.record(
                event_type,
                payload,
                actor="controller",
                idempotency_key=key,
            )
            if (
                event.get("event_type") != event_type
                or event.get("actor") != "controller"
                or event.get("idempotency_key") != key
                or event.get("payload") != payload
            ):
                raise RuntimeError("Project returned a mismatched portfolio event")
        project.materialize_views()
    except BaseException:
        if created and len(project.events()) == events_before:
            shutil.rmtree(Path(project.workspace).resolve() / bundle_root)
        raise
    event_count = len(project.events())
    body: JsonObject = {
        "contract": WORKFLOW_CONTRACT,
        "portfolio_id": portfolio["portfolio_id"],
        "portfolio_content_sha256": portfolio["portfolio_content_sha256"],
        "portfolio_status": portfolio["portfolio_status"],
        "council_disposition": portfolio["council_disposition"],
        "manifest": _descriptor(
            bundle_root / "manifest.json", files[Path("manifest.json")]
        ),
        "registered_event_count": 3,
        "project_event_count": event_count,
        "terminal_next_route_after_acceptance": portfolio["next_route"],
        "effects": dict(_ZERO_EFFECTS),
        "next_route": "accept-benchmark-portfolio-read-only",
    }
    digest = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    return {**body, "registration_content_sha256": digest, "registration_id": digest[:16]}


def register_benchmark_portfolio(
    project: ProjectPort, *, input_manifest_path: Path
) -> JsonObject:
    """Atomically register one terminal candidate in one workspace."""

    with _workspace_registration_transaction(project):
        return _register_benchmark_portfolio_locked(
            project, input_manifest_path=input_manifest_path
        )


__all__ = ["register_benchmark_portfolio"]
