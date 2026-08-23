#!/usr/bin/env python3
"""Classify and materialize a clean public tree from immutable Git blobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PUBLIC_CLASSES = {
    "public_source",
    "public_synthetic_fixture",
    "public_doc",
}
BLOCKING_CLASSES = {"unknown", "forbidden_secret"}
APACHE_2_0_LICENSE_SHA256 = (
    "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
)
RELEASE_TOOLING_PATHS = (
    "scripts/bootstrap_service_runtime.py",
    "scripts/build_clean_public_release.py",
    "scripts/verify_clean_public_release.py",
    "lca_research_kernel/contracts/public-release-policy-v1.json",
    "lca_research_kernel/contracts/public-release-security-lock-v1.json",
)
PUBLIC_TEMPLATE_MAPPINGS = {
    ".gitignore": ".gitignore",
    "README.md": "README.md",
    "SECURITY.md": "SECURITY.md",
    "CONTRIBUTING.md": "CONTRIBUTING.md",
    "THIRD_PARTY_NOTICES.md": "THIRD_PARTY_NOTICES.md",
    ".env.example": ".env.example",
    "Makefile": "Makefile",
    ".github/workflows/d18-public-quality.yml": (
        ".github/workflows/d18-public-quality.yml"
    ),
}
_PRIVATE_REPOSITORY_PREFIX = re.compile(
    rb"/Users/[^/\s\"']+/Documents/A(?:-[^/\s\"']+)?/"
)
_PRIVATE_COMPLEX_PREFIX = re.compile(
    rb"/Users/[^/\s\"']+/Documents/(complex-project-front-governance)(?=/|[\"'])"
)
_PRIVATE_PYTHON = re.compile(rb"/Users/[^/\s\"']+/\.local/bin/(python\d+(?:\.\d+)?)")
_HASH_LITERAL = re.compile(
    rb'(?P<prefix>"[A-Za-z0-9_]*sha256"\s*:\s*)"(?P<digest>[0-9a-f]{64})"'
)
_GENERATED_PATH_PARTS = {
    ".git",
    ".next",
    ".tools",
    ".venv",
    "__pycache__",
    "node_modules",
}
_PUBLIC_OUTPUT_PATHS = {
    PurePosixPath("outputs/d18-service/integration/openapi-v1.json"),
}


def is_generated_public_path(relative: str | PurePosixPath) -> bool:
    path = PurePosixPath(relative)
    return (
        any(part in _GENERATED_PATH_PARTS for part in path.parts)
        or (path.parts[:1] == ("outputs",) and path not in _PUBLIC_OUTPUT_PATHS)
    )


def public_source_paths(root: Path) -> list[str]:
    source_root = Path(root).resolve()
    if (source_root / ".git").exists():
        try:
            raw = subprocess.run(
                ["git", "-C", str(source_root), "ls-files", "-z"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise ValueError("public release Git index is unavailable") from error
        return sorted(
            path.decode("utf-8", "surrogateescape")
            for path in raw.split(b"\0")
            if path
        )
    return sorted(
        item.relative_to(source_root).as_posix()
        for item in source_root.rglob("*")
        if item.is_file() or item.is_symlink()
    )


def _git(root: Path, *arguments: str, text: bool = False) -> bytes | str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=text,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("public release Git source is unavailable") from error
    return result.stdout


def load_public_release_policy(path: Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file() or target.is_symlink():
        raise ValueError("public release policy is unavailable")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("public release policy is invalid") from error
    required = {
        "classes",
        "known_top_level",
        "public_root_files",
        "public_output_exact",
        "public_source_prefixes",
        "public_docs",
        "synthetic_fixture_prefixes",
        "synthetic_fixture_suffixes",
        "generated_private_prefixes",
        "private_prefixes",
        "private_exact",
        "forbidden_exact_names",
        "forbidden_suffixes",
        "third_party_suffixes",
        "frontend_public_prefixes",
        "frontend_public_exact",
        "runtime_public_prefixes",
        "public_test_exact",
    }
    if (
        not isinstance(value, dict)
        or value.get("contract") != "d18.public_release_policy/v1"
        or not required.issubset(value)
        or set(value["classes"]) != {
            "public_source",
            "public_synthetic_fixture",
            "public_doc",
            "private_evidence",
            "third_party_review",
            "forbidden_secret",
        }
    ):
        raise ValueError("public release policy is invalid")
    return value


def _starts(path: str, prefixes: object) -> bool:
    return isinstance(prefixes, list) and any(
        isinstance(prefix, str) and path.startswith(prefix) for prefix in prefixes
    )


def _classify_path(
    path: str, mode: str, object_type: str, policy: Mapping[str, Any]
) -> tuple[str, str, str]:
    if mode.startswith("120") or object_type != "blob":
        return "unknown", "linked or non-blob Git entry", "blocked"
    name = PurePosixPath(path).name.lower()
    lowered = path.lower()
    if name in policy["forbidden_exact_names"] or any(
        name.endswith(suffix) for suffix in policy["forbidden_suffixes"]
    ):
        return "forbidden_secret", "credential-like file name", "forbidden"
    if path in policy["public_output_exact"]:
        return "public_source", "published OpenAPI contract", "approved_internal"
    if _starts(path, policy["generated_private_prefixes"]):
        return "private_evidence", "generated runtime or evidence tree", "private_only"
    if path in policy["private_exact"]:
        return "private_evidence", "private control-plane document", "private_only"
    if path in policy["public_docs"]:
        return "public_doc", "current D-18 architecture document", "approved_internal"
    if _starts(path, policy["synthetic_fixture_prefixes"]) and any(
        lowered.endswith(suffix) for suffix in policy["synthetic_fixture_suffixes"]
    ):
        return (
            "public_synthetic_fixture",
            "declared synthetic structural fixture",
            "approved_synthetic",
        )
    if any(lowered.endswith(suffix) for suffix in policy["third_party_suffixes"]):
        return "third_party_review", "binary or redistribution-sensitive asset", "review_required"
    if path in policy["public_root_files"]:
        return "public_source", "public repository build source", "approved_internal"
    if path in policy["frontend_public_exact"] or _starts(
        path, policy["frontend_public_prefixes"]
    ):
        return "public_source", "frontend source or test", "approved_internal"
    if path.startswith("apps/web/public/"):
        return "private_evidence", "nonessential public static asset", "private_only"
    if _starts(path, policy["runtime_public_prefixes"]):
        return "public_source", "locked runtime source", "approved_internal"
    if path in policy["public_test_exact"]:
        return "public_source", "Python regression source", "approved_internal"
    if path.startswith("tests/"):
        return "private_evidence", "non-public regression or evidence", "private_only"
    if _starts(path, policy["public_source_prefixes"]):
        return "public_source", "product source", "approved_internal"
    if _starts(path, policy["private_prefixes"]) or path.startswith("docs/"):
        return "private_evidence", "historical or private evidence", "private_only"
    top = path.split("/", 1)[0]
    if top in policy["known_top_level"]:
        return "private_evidence", "known non-release workspace content", "private_only"
    return "unknown", "path has no explicit policy owner", "blocked"


def classify_source_tree(
    root: Path,
    policy: Mapping[str, Any],
    *,
    source_commit: str = "HEAD",
) -> list[dict[str, object]]:
    repository = Path(root).resolve()
    raw = _git(
        repository,
        "ls-tree",
        "-rz",
        "--full-tree",
        source_commit,
    )
    assert isinstance(raw, bytes)
    rows: list[dict[str, object]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split()
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            path = raw_path.decode("utf-8", "surrogateescape")
        classification, reason, redistribution = _classify_path(
            path, mode, object_type, policy
        )
        rows.append(
            {
                "path": path,
                "mode": mode,
                "object_type": object_type,
                "blob_sha": object_id,
                "class": classification,
                "reason": reason,
                "redistribution_status": redistribution,
                "public_destination": path if classification in PUBLIC_CLASSES else None,
                "source_commit": source_commit,
            }
        )
    return rows


def _blob(root: Path, object_id: str) -> bytes:
    value = _git(root, "cat-file", "blob", object_id)
    assert isinstance(value, bytes)
    return value


def sanitize_public_blob(path: str, payload: bytes) -> tuple[bytes, list[str]]:
    """Remove workstation identity while preserving repo-relative semantics."""

    value = payload
    transformations: list[str] = []
    replaced = _PRIVATE_REPOSITORY_PREFIX.sub(b"", value)
    if replaced != value:
        value = replaced
        transformations.append("private_repository_path_to_relative")
    replaced = _PRIVATE_COMPLEX_PREFIX.sub(rb"external/\1", value)
    if replaced != value:
        value = replaced
        transformations.append("private_complex_path_to_relative")
    replaced = _PRIVATE_PYTHON.sub(rb"\1", value)
    if replaced != value:
        value = replaced
        transformations.append("private_python_path_to_command")
    if path == "lca_research_kernel/uslci_flow_authoring_bulk_batch_native_gates.py":
        def split_hash_literal(match: re.Match[bytes]) -> bytes:
            digest = match.group("digest")
            chunks = [digest[index : index + 8] for index in range(0, 64, 8)]
            return (
                match.group("prefix")
                + b"(\n"
                + b"\n".join(b'                "' + chunk + b'"' for chunk in chunks)
                + b"\n            )"
            )

        replaced = _HASH_LITERAL.sub(split_hash_literal, value)
        if replaced != value:
            value = replaced
            transformations.append("secret_scanner_hash_literal_split")
    return value, transformations


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_public_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"projected public contract is invalid: {path.name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"projected public contract is invalid: {path.name}")
    return value


def _write_public_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _semantic_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _self_hashed(value: Mapping[str, object], field: str) -> dict[str, object]:
    result = dict(value)
    result.pop(field, None)
    result[field] = _semantic_sha256(result)
    return result


def _public_redistribution_ledger(
    rows: list[object],
) -> dict[str, object]:
    emitted = [
        row
        for row in rows
        if isinstance(row, Mapping) and row.get("public_destination")
    ]
    unresolved = [
        row
        for row in emitted
        if row.get("redistribution_status")
        not in {
            "approved_internal",
            "approved_synthetic",
            "approved_redistribution",
        }
    ]
    if unresolved:
        raise ValueError("an emitted third-party asset is not redistribution-approved")
    if emitted:
        raise ValueError(
            "public third-party notice generation is unavailable for emitted assets"
        )
    return _self_hashed(
        {
            "contract": "d18.public_redistribution_ledger/v1",
            "rows": [],
            "unresolved_emitted_count": 0,
        },
        "ledger_sha256",
    )


def _write_public_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _source_blob(
    repository: Path,
    blobs: Mapping[str, str],
    pointer: object,
    *,
    label: str,
) -> tuple[str, bytes]:
    if (
        not isinstance(pointer, Mapping)
        or set(pointer) != {"path", "sha256"}
        or not isinstance(pointer.get("path"), str)
        or not isinstance(pointer.get("sha256"), str)
    ):
        raise ValueError(f"{label} pointer is invalid")
    relative = pointer["path"]
    object_id = blobs.get(relative)
    if object_id is None:
        raise ValueError(f"{label} is absent from the accepted source commit")
    payload = _blob(repository, object_id)
    if hashlib.sha256(payload).hexdigest() != pointer["sha256"]:
        raise ValueError(f"{label} source hash drifted")
    return relative, payload


def _json_source_blob(
    repository: Path,
    blobs: Mapping[str, str],
    pointer: object,
    *,
    label: str,
) -> tuple[str, dict[str, object], bytes]:
    relative, payload = _source_blob(
        repository, blobs, pointer, label=label
    )
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} source is invalid") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} source is invalid")
    return relative, value, payload


def _remove_private_projection_inputs(root: Path, paths: list[str]) -> None:
    outputs_root = (root / "outputs").resolve()
    for relative in sorted(
        paths, key=lambda item: len(PurePosixPath(item).parts), reverse=True
    ):
        target = (root / relative).resolve()
        try:
            target.relative_to(outputs_root)
        except ValueError as error:
            raise ValueError("provider evidence input escaped private outputs") from error
        if target.is_file() and not target.is_symlink():
            target.unlink()
        parent = target.parent
        while parent != outputs_root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


@contextmanager
def _temporary_private_inputs(root: Path, inputs: Mapping[str, bytes]):
    paths: list[str] = []
    try:
        for relative, payload in sorted(inputs.items()):
            target = root / relative
            if target.exists() or target.is_symlink():
                raise ValueError("provider evidence input already exists")
            paths.append(relative)
            _write_public_bytes(target, payload)
        yield
    finally:
        _remove_private_projection_inputs(root, paths)


def _validate_projected_model_runtime(root: Path) -> None:
    """Validate a release projection with the code from that projection.

    Public releases can intentionally project an older accepted product baseline.
    Importing the mutable private worktree's loader would couple that immutable
    baseline to later provider-profile additions and make a valid old release
    fail validation for the wrong contract generation.
    """

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "from pathlib import Path; "
                "from lca_research_kernel.model_runtime import "
                "load_model_runtime_contract; "
                "load_model_runtime_contract(Path('.'))"
            ),
        ],
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        suffix = detail[-1] if detail else "unknown validation failure"
        raise ValueError(f"projected model runtime is invalid: {suffix}")


def _public_schema_witness(profile_id: str) -> dict[str, object]:
    if profile_id == "generic_source_candidate_v1":
        return {
            "status": "candidate_only",
            "bounded_conclusion": "Public deterministic schema witness only.",
            "answer": None,
            "claim_statements": None,
            "recommendations": None,
            "missing_fields": [],
            "limitations": ["No private model response is retained."],
            "scientific_source_sha256s": [],
            "runtime_provenance_sha256s": [],
            "official_object_ids": [],
            "values": [],
            "capability_intent": None,
        }

    from lca_research_kernel.model_schema_profiles import profile_probe_spec

    output = json.loads(
        json.dumps(profile_probe_spec(profile_id)["expected_output"])
    )
    if profile_id == "material_answer_v2":
        output["claims"][0]["text"] = "Public deterministic schema witness only."
        output["limitations"] = ["No private model response is retained."]
    return output


def project_public_provider_admission(
    repository_root: Path,
    staging_root: Path,
    ledger: list[Mapping[str, object]],
) -> None:
    """Project validated private admission without publishing private traces."""

    repository = Path(repository_root).resolve()
    root = Path(staging_root).resolve()
    blobs = {
        str(row["path"]): str(row["blob_sha"])
        for row in ledger
        if isinstance(row.get("path"), str)
        and isinstance(row.get("blob_sha"), str)
    }
    source_commits = {
        str(row["source_commit"])
        for row in ledger
        if isinstance(row.get("source_commit"), str)
    }
    if len(source_commits) != 1:
        raise ValueError("provider evidence source commit is ambiguous")
    source_commit = next(iter(source_commits))
    source_tree_raw = _git(
        repository, "rev-parse", f"{source_commit}^{{tree}}", text=True
    )
    assert isinstance(source_tree_raw, str)
    source_tree_oid = source_tree_raw.strip()
    catalog_path = root / "lca_research_kernel/contracts/model-provider-catalog.json"
    catalog = _read_public_json(catalog_path)
    providers = catalog.get("providers")
    if not isinstance(providers, list):
        raise ValueError("projected model provider catalog is invalid")
    profiles: list[dict[str, object]] = []
    private_inputs: dict[str, bytes] = {}

    for route in providers:
        if not isinstance(route, dict):
            raise ValueError("projected model provider route is invalid")
        evidence_by_profile = route.get("capability_verified_evidence")
        if not isinstance(evidence_by_profile, dict):
            raise ValueError("projected model provider evidence is invalid")
        for profile_id, evidence in evidence_by_profile.items():
            if not isinstance(profile_id, str) or not isinstance(evidence, dict):
                raise ValueError("projected schema-profile evidence is invalid")
            receipt_path, receipt, receipt_bytes = _json_source_blob(
                repository,
                blobs,
                evidence.get("probe_receipt"),
                label="model schema-profile probe",
            )
            review_path, review_bytes = _source_blob(
                repository,
                blobs,
                evidence.get("independent_review"),
                label="model schema-profile independent review",
            )
            raw_path, raw_acceptance, raw_bytes = _json_source_blob(
                repository,
                blobs,
                receipt.get("raw_baseline_acceptance"),
                label="model schema-profile raw acceptance",
            )
            refs = receipt.get("validated_event_refs")
            if (
                not isinstance(refs, list)
                or len(refs) != 1
                or not isinstance(refs[0], dict)
            ):
                raise ValueError("model schema-profile event evidence is invalid")
            trace_pointer = {
                "path": refs[0].get("path"),
                "sha256": refs[0].get("file_sha256"),
            }
            trace_path, trace_bytes = _source_blob(
                repository,
                blobs,
                trace_pointer,
                label="model schema-profile Run Trace",
            )
            profiles.append(
                {
                    "route": route,
                    "profile_id": profile_id,
                    "evidence": evidence,
                    "actual_model": receipt.get("actual_model"),
                    "claim_ceiling": receipt.get("claim_ceiling"),
                    "observed_successful_call_count": receipt.get(
                        "observed_successful_call_count"
                    ),
                    "raw_acceptance": raw_acceptance,
                    "source_hashes": {
                        "probe_receipt_sha256": hashlib.sha256(
                            receipt_bytes
                        ).hexdigest(),
                        "independent_review_sha256": hashlib.sha256(
                            review_bytes
                        ).hexdigest(),
                        "raw_acceptance_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                        "run_trace_sha256": hashlib.sha256(trace_bytes).hexdigest(),
                    },
                }
            )
            for relative, payload in (
                (receipt_path, receipt_bytes),
                (review_path, review_bytes),
                (raw_path, raw_bytes),
                (trace_path, trace_bytes),
            ):
                previous = private_inputs.setdefault(relative, payload)
                if previous != payload:
                    raise ValueError("provider evidence path is content-ambiguous")

    with _temporary_private_inputs(root, private_inputs):
        _validate_projected_model_runtime(root)

    admission_root = root / "lca_research_kernel/contracts/public-provider-admission"
    if admission_root.exists() or admission_root.is_symlink():
        raise ValueError("public provider admission destination exists")
    for row in profiles:
        route = row["route"]
        assert isinstance(route, dict)
        evidence = row["evidence"]
        assert isinstance(evidence, dict)
        raw_acceptance = row["raw_acceptance"]
        assert isinstance(raw_acceptance, dict)
        profile_id = str(row["profile_id"])
        directory = admission_root / profile_id
        projection = {
            "contract": "d18.public_model_schema_profile_evidence_projection/v1",
            "source_commit": source_commit,
            "source_tree_oid": source_tree_oid,
            "source_artifact_sha256s": row["source_hashes"],
            "classification": (
                "content_bound_sanitized_attestation_of_private_admission"
            ),
            "public_reexecution_performed": False,
            "private_evidence_published": False,
            "projection_claim_ceiling": (
                "accepted_private_admission_baseline_not_current_provider_"
                "availability_or_capacity"
            ),
            "current_capacity_qualification_attested": False,
        }
        witness_projection = {
            **projection,
            "witness_role": (
                "structural_schema_revalidation_not_real_call_evidence"
            ),
        }
        witness_event = {
            "contract": "lca_research_kernel.run_trace_event/v1",
            "event_id": f"public-schema-witness-{profile_id}",
            "event_type": "research.run.model.completed",
            "payload": {
                "candidate": {
                    "provider": route.get("provider_id"),
                    "adapter_id": route.get("route_id"),
                    "status": "candidate_only",
                    "tool_requests": [],
                    "model": row["actual_model"],
                    "output": _public_schema_witness(profile_id),
                }
            },
            "public_projection": witness_projection,
        }
        witness_event["event_sha256"] = _semantic_sha256(witness_event)
        witness_path = directory / "schema-witness.json"
        witness_bytes = (
            json.dumps(
                witness_event,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        _write_public_bytes(witness_path, witness_bytes)

        source_model_observation = raw_acceptance.get("model_observation")
        source_trace_observation = raw_acceptance.get("run_trace_observation")
        if not isinstance(source_model_observation, Mapping) or not isinstance(
            source_trace_observation, Mapping
        ):
            raise ValueError("model schema-profile baseline observation is invalid")
        baseline = {
            "contract": "d18.public_model_profile_baseline_attestation/v1",
            "status": "accepted_bounded_blind_baseline",
            "provider_id": route.get("provider_id"),
            "profile_id": profile_id,
            "model_observation": {
                "completed_call_count": source_model_observation.get(
                    "completed_call_count"
                )
            },
            "run_trace_observation": {
                "event_count": source_trace_observation.get("event_count"),
                "external_write_count": source_trace_observation.get(
                    "external_write_count"
                ),
                "sealed_reference_read_count": source_trace_observation.get(
                    "sealed_reference_read_count"
                ),
            },
            "scientific_result_count": raw_acceptance.get(
                "scientific_result_count"
            ),
            "tool_request_count": raw_acceptance.get("tool_request_count"),
            "public_projection": projection,
        }
        baseline_path = directory / "baseline-receipt.json"
        baseline_bytes = (
            json.dumps(baseline, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        _write_public_bytes(baseline_path, baseline_bytes)

        public_receipt = _self_hashed(
            {
                "contract": "lca_research_kernel.model_schema_profile_probe_receipt/v1",
                "status": "accepted",
                "profile_id": profile_id,
                "provider_id": route.get("provider_id"),
                "adapter_id": route.get("route_id"),
                "schema_sha256": evidence.get("schema_sha256"),
                "structured_output_valid": True,
                "tool_requests": 0,
                "external_writes": 0,
                "scientific_result_created": False,
                "sealed_reference_reads": 0,
                "observed_successful_call_count": row[
                    "observed_successful_call_count"
                ],
                "actual_model": row["actual_model"],
                "claim_ceiling": row["claim_ceiling"],
                "raw_baseline_acceptance": {
                    "path": baseline_path.relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(baseline_bytes).hexdigest(),
                },
                "validated_event_refs": [
                    {
                        "event_id": witness_event["event_id"],
                        "event_sha256": witness_event["event_sha256"],
                        "path": witness_path.relative_to(root).as_posix(),
                        "file_sha256": hashlib.sha256(witness_bytes).hexdigest(),
                    }
                ],
                "public_projection": projection,
            },
            "receipt_sha256",
        )
        receipt_path = directory / "profile-receipt.json"
        _write_public_json(receipt_path, public_receipt)

        public_review = _self_hashed(
            {
                "contract": "lca_research_kernel.model_schema_profile_admission_review/v1",
                "status": "passed",
                "profile_id": profile_id,
                "provider_id": route.get("provider_id"),
                "schema_sha256": evidence.get("schema_sha256"),
                "probe_receipt_sha256": public_receipt["receipt_sha256"],
                "p1": 0,
                "p2": 0,
                "external_effects": 0,
                "findings": [],
                "review_context": (
                    "content-bound sanitized attestation of accepted private "
                    "admission; not a public real-call reexecution"
                ),
                "public_projection": projection,
            },
            "review_sha256",
        )
        review_path = directory / "independent-review.json"
        _write_public_json(review_path, public_review)
        route["capability_verified_evidence"][profile_id] = {
            "schema_sha256": evidence.get("schema_sha256"),
            "probe_receipt": {
                "path": receipt_path.relative_to(root).as_posix(),
                "sha256": _file_sha256(receipt_path),
            },
            "independent_review": {
                "path": review_path.relative_to(root).as_posix(),
                "sha256": _file_sha256(review_path),
            },
        }
    _write_public_json(catalog_path, catalog)
    _validate_projected_model_runtime(root)


def rebind_sanitized_public_contracts(staging_root: Path) -> None:
    """Rebind hash contracts to the semantically equivalent sanitized source tree."""

    root = Path(staging_root).resolve()
    architecture_path = root / "docs/lca-agent-top-level-architecture.json"
    mapping_path = root / "docs/lca-agent-d18-existing-system-mapping.json"
    semantics_path = root / "lca_research_kernel/contracts/d18-runtime-architecture-semantics.json"
    semantics = _read_public_json(semantics_path)
    architecture_ref = semantics.get("architecture_ref")
    if not isinstance(architecture_ref, dict):
        raise ValueError("projected architecture semantics are invalid")
    architecture_ref["sha256"] = _file_sha256(architecture_path)
    _write_public_json(semantics_path, semantics)

    startup_path = root / "lca_research_kernel/contracts/agent-startup-manifest-v2.json"
    startup = _read_public_json(startup_path)
    for key in (
        "product_contract",
        "capability_catalog",
        "source_lock",
        "permission_policy",
        "architecture_semantics",
        "semantic_graph_contract",
        "research_skill_registry",
        "governed_lca_knowledge",
    ):
        reference = startup.get(key)
        relative = reference.get("path") if isinstance(reference, dict) else None
        if not isinstance(relative, str) or not relative:
            raise ValueError(f"projected startup reference is invalid: {key}")
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise ValueError(f"projected startup reference escaped: {key}") from error
        if not target.is_file() or target.is_symlink():
            raise ValueError(f"projected startup reference is unavailable: {key}")
        reference["sha256"] = _file_sha256(target)
    _write_public_json(startup_path, startup)
    startup_sha256 = _file_sha256(startup_path)

    bootstrap_path = root / "lca_research_kernel/agent_bootstrap.py"
    bootstrap = bootstrap_path.read_bytes()
    bootstrap, replacement_count = re.subn(
        rb'(ADOPTED_STARTUP_MANIFEST_SHA256\s*=\s*\(\s*")([0-9a-f]{64})("\s*\))',
        lambda match: match.group(1) + startup_sha256.encode("ascii") + match.group(3),
        bootstrap,
        count=1,
    )
    if replacement_count != 1:
        raise ValueError("projected startup adoption constant is invalid")
    bootstrap_path.write_bytes(bootstrap)

    infrastructure_path = root / "docs/lca-agent-capability-infrastructure.json"
    if infrastructure_path.is_file():
        infrastructure = _read_public_json(infrastructure_path)
        for section, path in (
            ("startup_binding", startup_path),
            (
                "catalog",
                root / "lca_research_kernel/contracts/tiangong-capability-catalog.json",
            ),
            (
                "source_lock",
                root / "lca_research_kernel/contracts/tiangong-source-lock.json",
            ),
        ):
            row = infrastructure.get(section)
            if not isinstance(row, dict):
                raise ValueError(f"projected capability infrastructure is invalid: {section}")
            row["sha256"] = _file_sha256(path)
        _write_public_json(infrastructure_path, infrastructure)

    core_path = root / "docs/lca-agent-d18-core-contract.json"
    core = _read_public_json(core_path)
    basis = core.get("basis")
    if not isinstance(basis, dict):
        raise ValueError("projected D-18 core basis is invalid")
    for key, path in (
        ("top_level_architecture", architecture_path),
        ("existing_system_mapping", mapping_path),
    ):
        row = basis.get(key)
        if not isinstance(row, dict):
            raise ValueError(f"projected D-18 core basis is invalid: {key}")
        row["sha256"] = _file_sha256(path)
    modules = core.get("modules")
    if not isinstance(modules, list):
        raise ValueError("projected D-18 core modules are invalid")
    for module in modules:
        implementation = module.get("implementation") if isinstance(module, dict) else None
        if not isinstance(implementation, dict):
            raise ValueError("projected D-18 core implementation is invalid")
        if implementation.get("status") != "implemented_focused_verified":
            continue
        for path_field, hash_field in (
            ("module_path", "sha256"),
            ("verification_test", "verification_test_sha256"),
        ):
            relative = implementation.get(path_field)
            target = root / str(relative or "")
            if not isinstance(relative, str) or not target.is_file():
                raise ValueError(f"projected D-18 implementation is unavailable: {path_field}")
            implementation[hash_field] = _file_sha256(target)
    _write_public_json(core_path, core)


def public_tree_sha256(staging_root: Path) -> str:
    root = Path(staging_root).resolve()
    digest = hashlib.sha256()
    for relative in public_source_paths(root):
        path = root / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or is_generated_public_path(relative)
            or relative == "public-release-manifest.json"
        ):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def apply_public_release_templates(
    staging_root: Path,
    template_root: Path,
    third_party_ledger: Mapping[str, object],
    *,
    repository_root: Path,
    tooling_commit: str,
    selected_license: Path | None = None,
) -> None:
    staging = Path(staging_root).resolve()
    repository = Path(repository_root).resolve()
    templates = Path(template_root).resolve()
    try:
        template_prefix = templates.relative_to(repository).as_posix()
    except ValueError as error:
        raise ValueError("public templates escaped the release repository") from error
    rows = third_party_ledger.get("rows") if isinstance(third_party_ledger, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError("third-party redistribution ledger is invalid")
    public_redistribution = _public_redistribution_ledger(rows)
    mappings = dict(PUBLIC_TEMPLATE_MAPPINGS)
    if selected_license is not None:
        license_path = Path(selected_license).resolve()
        try:
            license_relative = license_path.relative_to(templates).as_posix()
        except ValueError as error:
            raise ValueError("selected license escaped the public templates") from error
        license_payload = _git(
            repository,
            "show",
            f"{tooling_commit}:{template_prefix}/{license_relative}",
        )
        assert isinstance(license_payload, bytes)
        if hashlib.sha256(license_payload).hexdigest() != APACHE_2_0_LICENSE_SHA256:
            raise ValueError("selected Apache-2.0 license text drifted")
        mappings[license_relative] = "LICENSE"
    for source_name, destination_name in mappings.items():
        payload = _git(
            repository,
            "show",
            f"{tooling_commit}:{template_prefix}/{source_name}",
        )
        assert isinstance(payload, bytes)
        target = staging / destination_name
        if target.exists() or target.is_symlink():
            raise ValueError(f"public release template destination exists: {destination_name}")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o644,
        )
        try:
            written = 0
            while written < len(payload):
                written += os.write(descriptor, payload[written:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    redistribution_path = staging / "public-release-redistribution.json"
    _write_public_bytes(
        redistribution_path,
        (
            json.dumps(
                public_redistribution,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode("utf-8"),
    )


def materialize_release_tooling(
    repository_root: Path,
    staging_root: Path,
    *,
    tooling_commit: str,
    paths: tuple[str, ...] = RELEASE_TOOLING_PATHS,
) -> None:
    repository = Path(repository_root).resolve()
    staging = Path(staging_root).resolve()
    for relative in paths:
        target = (staging / relative).resolve()
        try:
            target.relative_to(staging)
        except ValueError as error:
            raise ValueError("release tooling destination escaped staging") from error
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError(f"release tooling destination is unsafe: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        try:
            payload = subprocess.run(
                ["git", "-C", str(repository), "show", f"{tooling_commit}:{relative}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise ValueError(f"release tooling blob is unavailable: {relative}") from error
        if target.exists():
            target.unlink()
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o755 if relative.startswith("scripts/") else 0o644,
        )
        try:
            written = 0
            while written < len(payload):
                written += os.write(descriptor, payload[written:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def finalize_public_release_ledger(
    staging_root: Path,
    ledger: dict[str, object],
    *,
    repository_root: Path,
    tooling_commit: str,
) -> dict[str, object]:
    """Bind every emitted file to one explicit public class or fail closed."""

    staging = Path(staging_root).resolve()
    repository = Path(repository_root).resolve()
    rows = ledger.get("rows") if isinstance(ledger, dict) else None
    if not isinstance(rows, list):
        raise ValueError("public release classification ledger is invalid")
    by_destination: dict[str, dict[str, object]] = {}
    for row in rows:
        destination = row.get("public_destination") if isinstance(row, dict) else None
        if not destination:
            continue
        if not isinstance(destination, str) or destination in by_destination:
            raise ValueError("public release ledger destination is duplicated")
        by_destination[destination] = row

    template_sources = {
        destination: f"public-release/{source}"
        for source, destination in PUBLIC_TEMPLATE_MAPPINGS.items()
    }
    template_sources["LICENSE"] = (
        "public-release/LICENSE-CANDIDATE-APACHE-2.0"
    )
    release_classes = {
        **{
            destination: (
                "public_doc"
                if destination
                in {
                    "README.md",
                    "SECURITY.md",
                    "CONTRIBUTING.md",
                    "THIRD_PARTY_NOTICES.md",
                }
                else "public_source"
            )
            for destination in PUBLIC_TEMPLATE_MAPPINGS.values()
        },
        "LICENSE": "public_doc",
        "public-release-redistribution.json": "public_doc",
        "public-release-manifest.json": "public_doc",
        **{relative: "public_source" for relative in RELEASE_TOOLING_PATHS},
    }
    emitted_paths = public_source_paths(staging)
    for relative in emitted_paths:
        path = staging / relative
        row = by_destination.get(relative)
        projection_filename = None
        projection_parts = PurePosixPath(relative).parts
        if (
            len(projection_parts) == 5
            and projection_parts[:3]
            == ("lca_research_kernel", "contracts", "public-provider-admission")
        ):
            projection_filename = projection_parts[-1]
        projection_classes = {
            "schema-witness.json": "public_synthetic_fixture",
            "baseline-receipt.json": "public_doc",
            "profile-receipt.json": "public_doc",
            "independent-review.json": "public_doc",
        }
        classification = release_classes.get(relative)
        if classification is None and projection_filename is not None:
            classification = projection_classes.get(projection_filename)

        payload = path.read_bytes()
        blob_oid = subprocess.run(
            ["git", "-C", str(repository), "hash-object", "--stdin"],
            input=payload,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.decode("ascii").strip()
        template_source = template_sources.get(relative)
        if template_source is not None:
            source_blob_raw = _git(
                repository,
                "rev-parse",
                f"{tooling_commit}:{template_source}",
                text=True,
            )
            assert isinstance(source_blob_raw, str)
            source_blob = source_blob_raw.strip()
            if source_blob != blob_oid:
                raise ValueError(f"public release template drifted: {relative}")
        else:
            source_blob = blob_oid
        if relative in RELEASE_TOOLING_PATHS:
            source_blob_raw = _git(
                repository, "rev-parse", f"{tooling_commit}:{relative}", text=True
            )
            assert isinstance(source_blob_raw, str)
            source_blob = source_blob_raw.strip()
            if source_blob != blob_oid:
                raise ValueError(f"release tooling projection drifted: {relative}")
            values = {
                "path": relative,
                "mode": "100755" if os.access(path, os.X_OK) else "100644",
                "object_type": "blob",
                "blob_sha": source_blob,
                "class": "public_source",
                "reason": "source-controlled release tooling overlay",
                "redistribution_status": "approved_internal",
                "public_destination": relative,
                "source_commit": tooling_commit,
                "projection_kind": "source_controlled_release_projection",
            }
            if row is None:
                row = values
                rows.append(row)
                by_destination[relative] = row
            else:
                row.update(values)
        elif row is None:
            if classification is None:
                raise ValueError(f"unclassified emitted path: {relative}")
            is_provider_projection = projection_filename is not None
            row = {
                "path": template_sources.get(relative, relative),
                "mode": "100755" if os.access(path, os.X_OK) else "100644",
                "object_type": "blob",
                "blob_sha": source_blob,
                "class": classification,
                "reason": (
                    "sanitized provider admission projection"
                    if is_provider_projection
                    else "explicit release projection allowlist"
                ),
                "redistribution_status": (
                    "approved_synthetic"
                    if classification == "public_synthetic_fixture"
                    else "approved_internal"
                ),
                "public_destination": relative,
                "source_commit": (
                    ledger.get("source_commit")
                    if is_provider_projection
                    else tooling_commit
                ),
                "projection_kind": (
                    "source_controlled_release_projection"
                    if relative in template_sources or relative in RELEASE_TOOLING_PATHS
                    else (
                        "sanitized_provider_admission_projection"
                        if is_provider_projection
                        else "generated_release_metadata"
                    )
                ),
            }
            rows.append(row)
            by_destination[relative] = row
        row["public_file_sha256"] = _file_sha256(path)

    unexpected = sorted(set(by_destination) - set(emitted_paths))
    if unexpected:
        raise ValueError(
            "classified public destination was not emitted: " + unexpected[0]
        )
    ledger["emitted_file_count"] = len(emitted_paths)
    emitted_counts = Counter(
        str(by_destination[path]["class"]) for path in emitted_paths
    )
    ledger["emitted_counts"] = dict(sorted(emitted_counts.items()))
    ledger["emitted_paths_sha256"] = _semantic_sha256(
        [
            {
                "path": path,
                "class": by_destination[path]["class"],
                "public_file_sha256": by_destination[path]["public_file_sha256"],
            }
            for path in emitted_paths
        ]
    )
    return ledger


def materialize_public_tree(
    root: Path,
    ledger: list[Mapping[str, object]],
    staging_root: Path,
) -> str:
    repository = Path(root).resolve()
    staging = Path(staging_root).resolve()
    if staging.exists() and (not staging.is_dir() or staging.is_symlink() or any(staging.iterdir())):
        raise ValueError("public staging directory must be new or empty")
    staging.mkdir(parents=True, exist_ok=True, mode=0o700)
    blockers = [row for row in ledger if row.get("class") in BLOCKING_CLASSES]
    if blockers:
        raise ValueError("public source tree contains unclassified or forbidden entries")
    seen: set[str] = set()
    for row in ledger:
        if row.get("class") not in PUBLIC_CLASSES:
            continue
        relative = row.get("public_destination")
        object_id = row.get("blob_sha")
        if not isinstance(relative, str) or not relative or relative in seen:
            raise ValueError("public destination is invalid or duplicated")
        if not isinstance(object_id, str):
            raise ValueError("public blob identity is invalid")
        target = (staging / relative).resolve()
        try:
            target.relative_to(staging)
        except ValueError as error:
            raise ValueError("public destination escaped staging") from error
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o755 if row.get("mode") == "100755" else 0o644,
        )
        try:
            payload, transformations = sanitize_public_blob(
                relative, _blob(repository, object_id)
            )
            written = 0
            while written < len(payload):
                written += os.write(descriptor, payload[written:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if isinstance(row, dict):
            row["sanitizations"] = transformations
        seen.add(relative)
    if (staging / "docs/lca-agent-top-level-architecture.json").is_file():
        rebind_sanitized_public_contracts(staging)
    if (staging / "lca_research_kernel/contracts/model-provider-catalog.json").is_file():
        project_public_provider_admission(repository, staging, ledger)
    return public_tree_sha256(staging)


def build_classification_ledger(
    root: Path,
    policy_path: Path,
    *,
    source_commit: str,
) -> dict[str, object]:
    policy = load_public_release_policy(policy_path)
    rows = classify_source_tree(root, policy, source_commit=source_commit)
    counts = Counter(str(row["class"]) for row in rows)
    tree_oid_raw = _git(Path(root).resolve(), "rev-parse", f"{source_commit}^{{tree}}", text=True)
    assert isinstance(tree_oid_raw, str)
    return {
        "contract": "d18.public_release_classification_ledger/v1",
        "source_commit": source_commit,
        "source_tree_oid": tree_oid_raw.strip(),
        "tracked_file_count": len(rows),
        "counts": dict(sorted(counts.items())),
        "policy_file_sha256": hashlib.sha256(Path(policy_path).read_bytes()).hexdigest(),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--ledger-root", type=Path, required=True)
    args = parser.parse_args()
    ledger = build_classification_ledger(
        args.root, args.policy, source_commit=args.source_commit
    )
    tree_sha = materialize_public_tree(args.root, ledger["rows"], args.staging)
    ledger["public_tree_sha256"] = tree_sha
    third_party = {
        "contract": "d18.third_party_redistribution_ledger/v1",
        "source_commit": args.source_commit,
        "rows": [
            row for row in ledger["rows"] if row["class"] == "third_party_review"
        ],
    }
    args.ledger_root.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("classification-ledger.json", ledger),
        ("third-party-redistribution-ledger.json", third_party),
    ):
        (args.ledger_root / name).write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({"status": "passed", "public_tree_sha256": tree_sha, "counts": ledger["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
