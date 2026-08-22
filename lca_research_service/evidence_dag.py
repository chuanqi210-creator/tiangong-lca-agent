"""Service-scoped Evidence DAG layered over accepted evaluation evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

from lca_research_evaluation.evidence_dag import (
    build_layer_lock,
    canonical_sha256,
    load_layer_lock,
    verify_layer_lock,
)

CONTRACT = "lca_research_service.evidence_bundle/v1"
REUSE_CONTRACT = "lca_research_service.evidence_reuse_receipt/v1"
_PRIOR_REUSE = Path(
    "outputs/d18-performance-benchmark/final/evidence-reuse-receipt.json"
)
_PRIOR_EXECUTION_LOCK = Path(
    "lca_research_kernel/contracts/evaluation-execution-lock.json"
)
_EXECUTION_EXACT = {
    "lca_research_kernel/research_orchestrator.py",
    "lca_research_kernel/context_assembler.py",
    "lca_research_kernel/lca_domain_kernel.py",
    "lca_research_kernel/model_gateway.py",
    "lca_research_kernel/model_runtime.py",
    "lca_research_kernel/capability_gateway.py",
    "lca_research_kernel/assurance.py",
    "agent/lca-project-controller.md",
}
_ACCEPTANCE_EXACT = {
    "CONTEXT.md",
    "lca_research_kernel/complex_runtime_alignment.py",
    "lca_research_kernel/lifecycle.py",
    "lca_research_kernel/product_interface.py",
    "lca_research_evaluation/finalizer.py",
    "lca_research_kernel/contracts/qingxiaoda-openai-compat-v1.json",
    "scripts/build_d18_service_integration.py",
    "scripts/check_d18_service_evidence.py",
    "tools/probe_d18_service_protocol.py",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_write(path: Path, value: Any) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
    temporary.replace(path)


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("service Evidence DAG requires readable Git objects")
    return result.stdout


def _changed_paths(root: Path, basis_commit: str) -> list[str]:
    paths = {
        line
        for line in _git(root, "diff", "--name-only", basis_commit, "--").splitlines()
        if line
    }
    for line in _git(
        root, "status", "--porcelain=v1", "--untracked-files=all"
    ).splitlines():
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            paths.add(path)
    return sorted(paths)


def classify_service_changes(paths: Iterable[str]) -> dict[str, Any]:
    rows = []
    route = "no_evidence_invalidation"
    priority = {
        "no_evidence_invalidation": 0,
        "rebuild_report_only": 1,
        "rerun_clean_acceptance": 2,
        "regrade_existing_traces": 3,
        "rerun_full_execution": 4,
        "unknown_change_fail_closed": 5,
    }
    for raw in sorted(set(paths)):
        path = raw.replace("\\", "/")
        if path in _EXECUTION_EXACT or path.startswith(
            ("agent/", "skills/", "runtime/model-python/", "runtime/tiangong-")
        ):
            selected = "rerun_full_execution"
            layer = "execution"
        elif path in {
            "lca_research_evaluation/grader.py",
            "lca_research_evaluation/metrics.py",
            "lca_research_evaluation/model_grader.py",
        }:
            selected = "regrade_existing_traces"
            layer = "scoring"
        elif (
            path in _ACCEPTANCE_EXACT
            or path.startswith(
                (
                    "lca_research_service/",
                    "tests/",
                    "outputs/d18-service/",
                    "outputs/council-default-lifecycle/",
                    "runtime/service-python/",
                    "runtime/evaluation-python/.venv",
                    "deploy/",
                )
            )
        ):
            selected = "rerun_clean_acceptance"
            layer = "acceptance"
        elif path.startswith("docs/"):
            selected = "rebuild_report_only"
            layer = "report"
        else:
            selected = "unknown_change_fail_closed"
            layer = "unknown"
        rows.append({"path": path, "layer": layer, "route": selected})
        if priority[selected] > priority[route]:
            route = selected
    return {
        "contract": "lca_research_service.affected_evidence/v1",
        "rows": rows,
        "rerun_route": route,
        "fail_closed": route == "unknown_change_fail_closed",
    }


def verify_product_interface_projection_only_change(
    root: Path, basis_commit: str
) -> dict[str, Any]:
    root = Path(root).resolve()
    relative = "lca_research_kernel/product_interface.py"
    base = _git(root, "show", f"{basis_commit}:{relative}")
    current = (root / relative).read_text()
    reconstructed = current
    replacements = (
        ("import hashlib\n", "", 1),
        (
            '                "project_record_ref": self._project_record_ref(record),\n',
            "",
            1,
        ),
        (
            '            "project_record_ref": self._project_record_ref(record),\n',
            "",
            2,
        ),
        (
            "\n    def _project_record_ref(self, record: ProjectRecord) -> dict[str, str]:\n"
            "        payload = record.projection_path.read_bytes()\n"
            "        return {\n"
            '            "path": record.projection_path.relative_to(self.root).as_posix(),\n'
            '            "sha256": hashlib.sha256(payload).hexdigest(),\n'
            '            "role": "project_record_projection",\n'
            "        }\n",
            "",
            1,
        ),
    )
    replacement_counts = {}
    for needle, replacement, expected_count in replacements:
        count = reconstructed.count(needle)
        replacement_counts[hashlib.sha256(needle.encode()).hexdigest()[:12]] = count
        if count != expected_count:
            return {
                "contract": "lca_research_service.projection_change_check/v1",
                "status": "failed",
                "reason": "projection-only patch shape drifted",
                "model_visible_change": current != base,
                "evaluation_runner_observes_added_field": (
                    "project_record_ref"
                    in (root / "lca_research_evaluation/d18_runner.py").read_text()
                ),
                "basis_commit": basis_commit,
                "current_sha256": _sha256(root / relative),
                "basis_sha256": hashlib.sha256(base.encode()).hexdigest(),
                "replacement_counts": replacement_counts,
            }
        reconstructed = reconstructed.replace(needle, replacement, expected_count)
    runner = (root / "lca_research_evaluation/d18_runner.py").read_text()
    passed = reconstructed == base and "project_record_ref" not in runner
    return {
        "contract": "lca_research_service.projection_change_check/v1",
        "status": "passed" if passed else "failed",
        "model_visible_change": reconstructed != base,
        "evaluation_runner_observes_added_field": "project_record_ref" in runner,
        "basis_commit": basis_commit,
        "current_sha256": _sha256(root / relative),
        "basis_sha256": hashlib.sha256(base.encode()).hexdigest(),
        "replacement_counts": replacement_counts,
    }


def verify_service_protocol_only_change(
    root: Path, basis_commit: str
) -> dict[str, Any]:
    root = Path(root).resolve()
    relative = "lca_research_kernel/contracts/qingxiaoda-openai-compat-v1.json"
    base = json.loads(_git(root, "show", f"{basis_commit}:{relative}"))
    current = json.loads((root / relative).read_text())
    reconstructed = json.loads(json.dumps(current))
    reconstructed["endpoints"] = [
        endpoint
        for endpoint in reconstructed["endpoints"]
        if endpoint
        not in {
            "GET /v1/research/{project_id}/jobs/latest",
            "POST /v1/jobs/{job_id}/inputs",
            "POST /v1/jobs/{job_id}/continuations",
            "GET /v1/jobs/{job_id}/result",
        }
    ]
    reconstructed["request"]["content_parts"] = [
        "text",
        "image_url.url",
        "input_audio.url",
        "file.url",
        "file.file_id",
    ]
    reconstructed["response"].pop("job_sse", None)
    reconstructed["response"].pop("protocol_probe", None)
    if (
        reconstructed["security"].get(
            "output_requires_project_record_registration"
        )
        == "scientific_and_research_outputs_only_protocol_probe_excluded"
    ):
        reconstructed["security"][
            "output_requires_project_record_registration"
        ] = True
    reconstructed["authentication"].pop("production_scope", None)
    reconstructed["authentication"].pop("cross_project_status", None)
    reconstructed["authentication"].pop("credential_source", None)
    runner = (root / "lca_research_evaluation/d18_runner.py").read_text()
    passed = (
        reconstructed == base
        and "qingxiaoda-openai-compat-v1" not in runner
    )
    return {
        "contract": "lca_research_service.protocol_change_check/v1",
        "status": "passed" if passed else "failed",
        "basis_commit": basis_commit,
        "current_sha256": _sha256(root / relative),
        "basis_sha256": hashlib.sha256(
            (_git(root, "show", f"{basis_commit}:{relative}")).encode()
        ).hexdigest(),
        "evaluation_runner_observes_protocol": (
            "qingxiaoda-openai-compat-v1" in runner
        ),
    }


def verify_complex_alignment_stage_only_change(
    root: Path, basis_commit: str
) -> dict[str, Any]:
    root = Path(root).resolve()
    relative = "lca_research_kernel/complex_runtime_alignment.py"
    base = _git(root, "show", f"{basis_commit}:{relative}")
    current = (root / relative).read_text()
    added_constants = (
        'STAGE_FREEZE_PATH = Path("docs/complex-runtime-stage-freeze-2026-07-25.json")\n'
        'STAGE_FREEZE_CONTRACT = "lca_research_kernel.complex_runtime_stage_freeze/v1"\n'
        'STAGE_FREEZE_OBJECT = "9a9d28ce82a633ed2e915650633941adafa5fda4"\n'
        "STAGE_FREEZE_MANIFEST_SHA256 = (\n"
        '    "4f2c96a81621319a9da1092e6106e9ae33b93bde3d9f357b2e8ff8572ec998a7"\n'
        ")\n"
        "STAGE_FREEZE_ARCHIVE_SHA256 = (\n"
        '    "f06a55dfddb5af65dfae0aabb0295c29587af55b62b67882bbffca1999fb2214"\n'
        ")\n"
    )
    legacy_block = (
        "    pointer_pattern = re.compile(\n"
        '        rf"^complex_runtime_adoption: {re.escape(str(ADOPTION_PATH))}#sha256=([0-9a-f]{{64}})$",\n'
        "        flags=re.MULTILINE,\n"
        "    )\n"
        "    pointer = pointer_pattern.search(context)\n"
        "    if pointer is None:\n"
        '        errors.append("CONTEXT.md has no pinned Complex adoption pointer")\n'
        "    elif pointer.group(1) != _sha256(adoption_path):\n"
        '        errors.append("CONTEXT.md Complex adoption pointer hash drifted")\n'
        '    if f"complex_runtime_pin: {PINNED_REVISION}" not in context:\n'
        '        errors.append("CONTEXT.md has no exact Complex runtime pin")\n'
    )
    marker_start = "    stage_pointer_pattern = re.compile(\n"
    marker_end = '    if "phase_goal:" not in context:\n'
    relocation_block = (
        '    override = os.environ.get("D18_COMPLEX_REPOSITORY")\n'
        '    candidates = [Path(override)] if override else [declared]\n'
    )
    legacy_relocation_block = "    candidates = [declared]\n"
    start = current.find(marker_start)
    end = current.find(marker_end)
    shape_valid = (
        current.count(added_constants) == 1
        and current.count("import os\n") == 1
        and current.count(relocation_block) == 1
        and current.count("    if not override and parent.is_dir():\n") == 1
        and start >= 0
        and end > start
    )
    reconstructed = current
    if shape_valid:
        reconstructed = reconstructed.replace("import os\n", "", 1)
        reconstructed = reconstructed.replace(
            relocation_block, legacy_relocation_block, 1
        )
        reconstructed = reconstructed.replace(
            "    if not override and parent.is_dir():\n",
            "    if parent.is_dir():\n",
            1,
        )
        reconstructed = reconstructed.replace(added_constants, "", 1)
        start = reconstructed.find(marker_start)
        end = reconstructed.find(marker_end)
        reconstructed = reconstructed[:start] + legacy_block + reconstructed[end:]
    evaluation_sources = "\n".join(
        path.read_text()
        for path in sorted((root / "lca_research_evaluation").glob("*.py"))
    )
    passed = (
        shape_valid
        and reconstructed == base
        and "import complex_runtime_alignment" not in evaluation_sources
        and "from lca_research_kernel.complex_runtime_alignment" not in evaluation_sources
    )
    return {
        "contract": "lca_research_service.complex_alignment_change_check/v1",
        "status": "passed" if passed else "failed",
        "basis_commit": basis_commit,
        "current_sha256": _sha256(root / relative),
        "basis_sha256": hashlib.sha256(base.encode()).hexdigest(),
        "evaluation_imports_alignment": (
            "import complex_runtime_alignment" in evaluation_sources
            or "from lca_research_kernel.complex_runtime_alignment"
            in evaluation_sources
        ),
    }


def verify_evaluation_finalizer_overlay_only_change(
    root: Path, basis_commit: str
) -> dict[str, Any]:
    root = Path(root).resolve()
    relative = "lca_research_evaluation/finalizer.py"
    base = _git(root, "show", f"{basis_commit}:{relative}")
    current = (root / relative).read_text()
    added_constant = (
        "SERVICE_EVIDENCE_OVERLAY = Path(\n"
        '    "outputs/d18-service/product-interface-binding/final-evidence"\n'
        ")\n"
    )
    legacy_function = (
        "def verify_terminal_evidence_dag(root: Path) -> dict[str, Any]:\n"
        '    """Verify the current layered invalidation authority for terminal evidence."""\n'
        "\n"
        "    return verify_evidence_dag(\n"
        "        root,\n"
        "        lock_paths=EVIDENCE_LOCK_PATHS,\n"
        "        reuse_path=EVIDENCE_REUSE_RECEIPT,\n"
        "        raw_root=FINAL_RAW_EVIDENCE,\n"
        "    )\n"
        "\n"
        "\n"
    )
    added_acceptance_field = (
        '            "service_reuse_overlay": deepcopy(\n'
        '                evidence_dag.get("service_reuse_overlay")\n'
        "            ),\n"
    )
    marker_start = "def verify_terminal_evidence_dag(root: Path) -> dict[str, Any]:\n"
    marker_end = "def _copy_bound_file("
    shape_valid = (
        current.count(added_constant) == 1
        and current.count(added_acceptance_field) == 1
        and current.find(marker_start) >= 0
        and current.find(marker_end) > current.find(marker_start)
    )
    reconstructed = current
    if shape_valid:
        reconstructed = reconstructed.replace(added_constant, "", 1)
        reconstructed = reconstructed.replace(added_acceptance_field, "", 1)
        start = reconstructed.find(marker_start)
        end = reconstructed.find(marker_end)
        reconstructed = reconstructed[:start] + legacy_function + reconstructed[end:]
    runner = (root / "lca_research_evaluation/d18_runner.py").read_text()
    passed = (
        shape_valid
        and reconstructed == base
        and "verify_terminal_evidence_dag" not in runner
    )
    return {
        "contract": "lca_research_service.evaluation_finalizer_overlay_check/v1",
        "status": "passed" if passed else "failed",
        "basis_commit": basis_commit,
        "current_sha256": _sha256(root / relative),
        "basis_sha256": hashlib.sha256(base.encode()).hexdigest(),
        "evaluation_runner_observes_overlay": (
            "verify_terminal_evidence_dag" in runner
        ),
    }


def _lock_paths(root: Path, paths: Iterable[str], prefix: str) -> list[str]:
    return sorted(
        path
        for path in set(paths)
        if path.startswith(prefix) and (root / path).is_file()
    )


def _verify_prior_execution_inputs(
    root: Path,
    lock: dict[str, Any],
    *,
    allowed_changed_paths: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    mismatches: list[str] = []
    checked = 0
    for row in lock.get("inputs", []):
        path = row.get("path")
        digest = row.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str):
            mismatches.append("<invalid-lock-row>")
            continue
        if path in allowed_changed_paths:
            continue
        target = root / path
        checked += 1
        if not target.is_file() or _sha256(target) != digest:
            mismatches.append(path)
    return {
        "contract": "lca_research_service.prior_execution_snapshot_check/v1",
        "status": "passed" if not mismatches else "failed",
        "checked_input_count": checked,
        "allowed_changed_paths": sorted(allowed_changed_paths),
        "mismatches": mismatches,
    }


def verify_prior_execution_snapshot(
    root: Path, basis_commit: str
) -> dict[str, Any]:
    root = Path(root).resolve()
    lock_path = root / _PRIOR_EXECUTION_LOCK
    lock = load_layer_lock(lock_path)
    projection = verify_product_interface_projection_only_change(
        root, basis_commit
    )
    protocol = verify_service_protocol_only_change(root, basis_commit)
    complex_alignment = verify_complex_alignment_stage_only_change(
        root, basis_commit
    )
    evaluation_finalizer = verify_evaluation_finalizer_overlay_only_change(
        root, basis_commit
    )
    inputs = _verify_prior_execution_inputs(
        root,
        lock,
        allowed_changed_paths=frozenset(
            {
                "lca_research_kernel/product_interface.py",
                "lca_research_kernel/contracts/qingxiaoda-openai-compat-v1.json",
                "lca_research_kernel/complex_runtime_alignment.py",
                "lca_research_evaluation/finalizer.py",
            }
        ),
    )
    status = (
        "passed"
        if (
            projection["status"] == "passed"
            and protocol["status"] == "passed"
            and complex_alignment["status"] == "passed"
            and evaluation_finalizer["status"] == "passed"
            and inputs["status"] == "passed"
        )
        else "failed"
    )
    return {
        "contract": "lca_research_service.prior_execution_snapshot_check/v1",
        "status": status,
        "lock_ref": {
            "path": _PRIOR_EXECUTION_LOCK.as_posix(),
            "sha256": _sha256(lock_path),
        },
        "lock_sha256": lock["lock_sha256"],
        "projection_exception": projection,
        "protocol_exception": protocol,
        "complex_alignment_exception": complex_alignment,
        "evaluation_finalizer_exception": evaluation_finalizer,
        "input_closure": inputs,
    }


def build_service_evidence_bundle(
    root: Path, *, basis_commit: str, output_root: Path
) -> dict[str, Any]:
    root = Path(root).resolve()
    output_root = Path(output_root)
    changed = _changed_paths(root, basis_commit)
    affected = classify_service_changes(changed)
    prior_execution = verify_prior_execution_snapshot(root, basis_commit)
    projection_check = prior_execution["projection_exception"]
    if projection_check["status"] != "passed":
        raise ValueError("Product Interface change is not projection-only")
    if prior_execution["status"] != "passed":
        raise ValueError("prior execution snapshot drifted")
    if affected["rerun_route"] in {
        "rerun_full_execution",
        "unknown_change_fail_closed",
    }:
        raise ValueError("service change reaches prior evaluation execution")

    service_inputs = _lock_paths(root, changed, "lca_research_service/")
    service_inputs.extend(
        path
        for path in (
            "lca_research_kernel/product_interface.py",
            "lca_research_kernel/contracts/qingxiaoda-openai-compat-v1.json",
            "lca_research_kernel/complex_runtime_alignment.py",
            "lca_research_evaluation/finalizer.py",
        )
        if (root / path).is_file()
    )
    service_inputs = sorted(set(service_inputs))
    execution_lock = build_layer_lock(
        root,
        layer="execution",
        paths=service_inputs,
        basis={
            "basis_commit": basis_commit,
            "scope": "service_product_interface_runtime_only",
        },
        evidence={
            "projection_change_check": projection_check,
            "prior_execution_snapshot": prior_execution,
        },
    )
    _canonical_write(output_root / "service-execution-lock.json", execution_lock)

    acceptance_inputs = sorted(
        {
            path
            for path in changed
            if (
                path.startswith("tests/test_service_")
                or path.startswith("outputs/d18-service/")
                or path
                in {
                    "scripts/build_d18_service_integration.py",
                    "tools/probe_d18_service_protocol.py",
                }
            )
            and (root / path).is_file()
            and path.rsplit("/", 1)[-1]
            not in {
                "service-execution-lock.json",
                "service-acceptance-lock.json",
                "service-evidence-reuse-receipt.json",
            }
        }
    )
    acceptance_lock = build_layer_lock(
        root,
        layer="acceptance",
        paths=acceptance_inputs,
        basis={
            "service_execution_lock_sha256": execution_lock["lock_sha256"],
            "scope": "service_acceptance_only",
        },
        evidence={"affected_report": affected},
    )
    _canonical_write(output_root / "service-acceptance-lock.json", acceptance_lock)

    prior_path = root / _PRIOR_REUSE
    prior = json.loads(prior_path.read_text())
    if (
        prior.get("status") != "accepted"
        or prior.get("observed_run_count") != 270
        or prior.get("raw_tree_unchanged") is not True
    ):
        raise ValueError("accepted 270-run reuse receipt is invalid")
    reuse = {
        "contract": REUSE_CONTRACT,
        "status": "accepted",
        "basis_commit": basis_commit,
        "prior_receipt": {
            "path": _PRIOR_REUSE.as_posix(),
            "sha256": _sha256(prior_path),
        },
        "prior_run_count": prior["observed_run_count"],
        "prior_failure_count": prior["observed_failure_count"],
        "prior_raw_tree": prior["raw_tree"],
        "prior_raw_evidence_preserved_by_receipt": True,
        "prior_execution_snapshot": prior_execution,
        "cross_version_splicing": False,
        "product_interface_projection_check": projection_check,
        "affected_report": affected,
        "rerun_route": "rerun_clean_acceptance",
        "reason": (
            "Service transport, control, and additive Project Record reference "
            "projection changed, including stricter service project isolation; "
            "the prior evaluation orchestrator, model-visible inputs, prompts, "
            "model/tool authorization policy, scorer, and immutable raw-run "
            "receipt did not."
        ),
    }
    reuse["receipt_sha256"] = canonical_sha256(reuse)
    _canonical_write(output_root / "service-evidence-reuse-receipt.json", reuse)
    return reuse


def verify_service_evidence_bundle(root: Path, output_root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    output_root = Path(output_root)
    execution = load_layer_lock(output_root / "service-execution-lock.json")
    acceptance = load_layer_lock(output_root / "service-acceptance-lock.json")
    reuse = json.loads(
        (output_root / "service-evidence-reuse-receipt.json").read_text()
    )
    body = dict(reuse)
    stored = body.pop("receipt_sha256", None)
    prior = reuse.get("prior_receipt", {})
    prior_path = root / str(prior.get("path", ""))
    basis_commit = reuse.get("basis_commit")
    current_affected = (
        classify_service_changes(_changed_paths(root, basis_commit))
        if isinstance(basis_commit, str)
        else {"rerun_route": "unknown_change_fail_closed"}
    )
    current_projection = (
        verify_prior_execution_snapshot(root, basis_commit)
        if isinstance(basis_commit, str)
        else {"status": "failed"}
    )
    locked_execution_paths = {
        row["path"] for row in execution.get("inputs", [])
    }
    current_service_paths = {
        row["path"]
        for row in current_affected.get("rows", [])
        if row["path"].startswith("lca_research_service/")
        and (root / row["path"]).is_file()
    }
    checks = {
        "execution_lock": verify_layer_lock(root, execution)["status"] == "valid",
        "acceptance_lock": verify_layer_lock(root, acceptance)["status"] == "valid",
        "lock_link": (
            acceptance["basis"]["service_execution_lock_sha256"]
            == execution["lock_sha256"]
        ),
        "receipt_self_hash": stored == canonical_sha256(body),
        "prior_receipt": (
            prior_path.is_file()
            and _sha256(prior_path) == prior.get("sha256")
        ),
        "run_count": reuse.get("prior_run_count") == 270,
        "route": reuse.get("rerun_route") == "rerun_clean_acceptance",
        "no_splicing": reuse.get("cross_version_splicing") is False,
        "current_affected_scope": current_affected.get("rerun_route")
        not in {"rerun_full_execution", "unknown_change_fail_closed"},
        "current_prior_execution_snapshot": current_projection.get("status")
        == "passed",
        "all_changed_service_inputs_locked": current_service_paths.issubset(
            locked_execution_paths
        ),
        "stored_prior_execution_snapshot": reuse.get(
            "prior_execution_snapshot", {}
        ).get("status")
        == "passed",
    }
    return {
        "contract": "lca_research_service.evidence_bundle_check/v1",
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
    }
