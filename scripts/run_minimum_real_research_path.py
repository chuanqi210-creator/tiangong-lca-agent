#!/usr/bin/env python3
"""Run exactly one contract-bound minimum-path task or resume its checkpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.minimum_real_path import (
    validate_minimum_real_path_contract,
)
from lca_research_evaluation.minimum_real_path_runner import (
    MinimumRealPathRunner,
)
from lca_research_kernel.model_gateway import ModelGateway
from lca_research_kernel.provider_capacity import (
    validate_provider_capacity_qualification,
)
from lca_research_service.runtime_factory import RuntimeFactory

FAKE_CONTRACT = "minimum_real_path_fake_runtime/v1"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one D-18 minimum real research path task."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--runtime-data-root", type=Path, required=True)
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--question-pack", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--question-id", choices=("Q1", "Q9", "Q5"), required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--fake-runtime-fixture", type=Path)
    return parser


def _load_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return deepcopy(dict(value))


def _under_root(root: Path, path: Path, label: str) -> Path:
    root = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} escaped the repository root") from error
    return resolved


def _resume_run_id(execution_root: Path, question_id: str) -> str:
    checkpoint = _load_object(
        Path(execution_root) / question_id / "checkpoint.json",
        "minimum-path resume checkpoint",
    )
    trace_ref = checkpoint.get("run_trace_ref")
    run_id = trace_ref.get("run_id") if isinstance(trace_ref, Mapping) else None
    path = trace_ref.get("path") if isinstance(trace_ref, Mapping) else None
    if (
        checkpoint.get("contract") != "minimum_path_checkpoint/v1"
        or checkpoint.get("question_id") != question_id
        or not isinstance(trace_ref, Mapping)
        or trace_ref.get("contract")
        != "lca_research_kernel.run_trace_ref/v1"
        or not isinstance(run_id, str)
        or RUN_ID_RE.fullmatch(run_id) is None
        or not isinstance(path, str)
        or Path(path).name != f"{run_id}.jsonl"
    ):
        raise ValueError("minimum-path resume run identity is invalid")
    return run_id


class _SequenceAdapter:
    provider_id = "fake"

    def __init__(self, outputs: list[Mapping[str, object]]):
        self.outputs = [deepcopy(dict(output)) for output in outputs]
        self.calls = 0

    def capabilities(self) -> dict:
        return {
            "provider": "fake",
            "adapter_id": "fake-minimum-path",
            "configured_model_id": "deterministic-minimum-path",
            "structured_output": True,
            "streaming": True,
            "tool_requests": True,
            "generic_source_candidate_v1": True,
            "remote_execution": False,
        }

    def reason(self, request: Mapping[str, object]) -> tuple[dict, dict]:
        if self.calls >= len(self.outputs):
            raise RuntimeError("fake minimum-path outputs are exhausted")
        output = deepcopy(self.outputs[self.calls])
        self.calls += 1
        return output, {
            "provider": "fake",
            "adapter_id": "fake-minimum-path",
            "provider_response_id": f"fake-minimum-path-{self.calls}",
            "model": "deterministic-minimum-path",
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "tool_requests": [],
            "duration_ms": 0,
            "cost": None,
        }


class _FixtureTransport:
    def __init__(self, bodies: Mapping[str, object]):
        if any(
            not isinstance(url, str) or not isinstance(body, str)
            for url, body in bodies.items()
        ):
            raise ValueError("fake official-source bodies are invalid")
        self.bodies = dict(bodies)

    def request(
        self,
        method: str,
        url: str,
        *,
        timeout_seconds: float,
        max_bytes: int,
        headers: Mapping[str, object],
    ) -> dict:
        if method != "GET" or url not in self.bodies:
            raise RuntimeError("fake official source is unavailable")
        body = self.bodies[url].encode("utf-8")
        if len(body) > max_bytes:
            raise RuntimeError("fake official source exceeds the byte budget")
        return {
            "status": 200,
            "headers": {
                "Content-Type": "text/html; charset=utf-8",
                "ETag": (
                    '"'
                    + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
                    + '"'
                ),
            },
            "body": body,
        }


def _fake_runtime(
    *,
    root: Path,
    data_root: Path,
    fixture_path: Path,
    question_id: str,
    run_id: str | None,
    clock,
) -> object:
    fixture = _load_object(fixture_path, "fake runtime fixture")
    tasks = fixture.get("tasks")
    bodies = fixture.get("official_source_bodies")
    task = tasks.get(question_id) if isinstance(tasks, Mapping) else None
    outputs = task.get("model_outputs") if isinstance(task, Mapping) else None
    if (
        fixture.get("contract") != FAKE_CONTRACT
        or not isinstance(outputs, list)
        or not outputs
        or any(not isinstance(output, Mapping) for output in outputs)
        or not isinstance(bodies, Mapping)
    ):
        raise ValueError("fake runtime fixture is invalid")
    factory = RuntimeFactory(
        repository_root=root,
        data_root=data_root,
        model_gateway=ModelGateway(
            {"fake": _SequenceAdapter(outputs)},
            default_provider="fake",
            clock=clock,
        ),
        official_source_transport=_FixtureTransport(bodies),
        run_id=run_id,
        clock=clock,
    )
    # Resuming an already accepted task is read-only.  Reuse the accepted
    # Project Record directly instead of reopening the unrelated material
    # control database, whose SQLite headers may change on a no-op connection.
    if run_id is not None:
        from lca_research_kernel.agent_bootstrap import AgentBootstrap
        from lca_research_kernel.product_interface import ProductInterface

        startup_context = AgentBootstrap.load(
            root,
            root / "lca_research_kernel/contracts/agent-startup-manifest-v2.json",
        )
        return ProductInterface(
            data_root / "runtime",
            model_gateway=factory.model_gateway,
            assurance=None,
            clock=clock,
            run_id=run_id,
            startup_context=startup_context,
        )
    binding = factory.build()
    if not binding.ready or binding.runtime is None:
        raise ValueError("fake Product Interface runtime is unavailable")
    return binding.runtime.product_interface


def run(args: argparse.Namespace, *, clock=_utc_now) -> dict:
    root = Path(args.root).resolve()
    runtime_data_root = _under_root(
        root, args.runtime_data_root, "runtime data root"
    )
    execution_root = _under_root(
        root, args.execution_root, "execution root"
    )
    contract_path = _under_root(root, args.contract, "minimum-path contract")
    pack_path = _under_root(root, args.question_pack, "question pack")
    qualification_path = _under_root(
        root, args.qualification, "provider qualification"
    )
    contract = _load_object(contract_path, "minimum-path contract")
    pack_ref = contract.get("question_pack")
    if (
        not isinstance(pack_ref, Mapping)
        or pack_path != (root / str(pack_ref.get("path", ""))).resolve()
    ):
        raise ValueError("question pack does not match the minimum-path contract")
    validate_minimum_real_path_contract(
        contract,
        question_pack_path=pack_path,
    )
    qualification = validate_provider_capacity_qualification(
        _load_object(qualification_path, "provider qualification")
    )
    resume_run_id = (
        _resume_run_id(execution_root, args.question_id)
        if args.resume
        else None
    )
    if args.fake_runtime_fixture is not None:
        if not args.test_mode:
            raise ValueError("fake runtime requires explicit test mode")
        product = _fake_runtime(
            root=root,
            data_root=runtime_data_root,
            fixture_path=_under_root(
                root,
                args.fake_runtime_fixture,
                "fake runtime fixture",
            ),
            question_id=args.question_id,
            run_id=resume_run_id,
            clock=clock,
        )
    else:
        if args.test_mode:
            raise ValueError("test mode requires a fake runtime fixture")
        binding = RuntimeFactory(
            repository_root=root,
            data_root=runtime_data_root,
            capacity_qualification=qualification,
            run_id=resume_run_id,
            clock=clock,
        ).build()
        if not binding.ready or binding.runtime is None:
            raise ValueError("Product Interface runtime is unavailable")
        product = binding.runtime.product_interface
    runner = MinimumRealPathRunner(
        product_interface=product,
        contract=contract,
        output_root=execution_root,
        clock=clock,
    )
    return (
        runner.resume_one(args.question_id)
        if args.resume
        else runner.run_one(
            args.question_id,
            qualification=qualification,
        )
    )


def main() -> int:
    args = _parser().parse_args()
    try:
        report = run(args)
    except (OSError, RuntimeError, ValueError) as error:
        print(
            json.dumps(
                {
                    "status": "invalid_or_unavailable",
                    "failure_code": type(error).__name__,
                    "executed_external_effects": 0,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    if report["status"] == "provider_capacity_deferred":
        return 10
    if report["status"] in {
        "formal_result_accepted",
        "formal_limited_result_accepted",
    }:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
