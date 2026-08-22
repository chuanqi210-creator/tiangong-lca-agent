"""Explicitly non-production Product Interface mock for frontend integration."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from lca_research_kernel.material_resolution import MaterialResolutionBinding

from fastapi import Header, HTTPException

from .app import ServiceConfig, create_app
from .no_effect_supervisor import NoEffectWorkerSupervisor
from .runtime import ProductInterfaceRuntime

MOCK_CONTRACT = "lca_research_service.no_effect_mock/v1"
_MODES = frozenset({"complete", "waiting", "bounded", "failed"})
_MATERIAL_FIXTURE_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "outputs/d18-service/integration/material-fixtures/manifest.json"
)


def replay_material_fixture(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Execute a deterministic, no-effect fixture state transition sequence."""

    if set(fixture) != {"kind", "input", "events", "expected"}:
        raise ValueError("material fixture is invalid")
    events = fixture.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("material fixture events are invalid")
    states = []
    previous = 0
    projection: dict[str, Any] = {"input": deepcopy(dict(fixture["input"]))}
    for raw in events:
        if not isinstance(raw, Mapping):
            raise ValueError("material fixture event is invalid")
        event = deepcopy(dict(raw))
        sequence = event.get("sequence")
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence <= previous
            or not isinstance(event.get("event"), str)
            or not event["event"]
        ):
            raise ValueError("material fixture event order is invalid")
        projection = {**projection, **event}
        states.append(deepcopy(projection))
        previous = sequence
    return {
        "contract": "lca_research_service.material_fixture_replay/v1",
        "states": states,
        "final": {
            "sequence": previous,
            "event": projection["event"],
            "expected": deepcopy(dict(fixture["expected"])),
        },
    }


class NoEffectMockProductInterface:
    """Deterministic state fixture; it never calls a model, tool, or network."""

    def __init__(
        self,
        root: Path,
        *,
        modes: Mapping[str, str] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.clock = lambda: "2026-07-25T04:00:00Z"
        self.modes = dict(modes or {})
        if set(self.modes.values()) - _MODES:
            raise ValueError("unsupported mock scenario")
        self.states: dict[str, dict[str, Any]] = {}
        self.material_resolution_binding = MaterialResolutionBinding()
        self.effects = {
            "model_calls": 0,
            "tool_calls": 0,
            "external_writes": 0,
            "scientific_results": 0,
        }

    def submit_research(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = deepcopy(dict(request))
        input_binding_ref = normalized.pop("input_binding_ref", None)
        if input_binding_ref is None:
            return self.start_research(normalized)
        bundles = self.material_resolution_binding.resolve_material_selection(
            str(normalized["research_id"]), input_binding_ref
        )
        return self.start_research_with_materials(normalized, bundles)

    def submit_input(
        self,
        research_id: str,
        external_input: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = deepcopy(dict(external_input))
        input_binding_ref = normalized.pop("input_binding_ref", None)
        binding = None
        if input_binding_ref is not None:
            binding = self.material_resolution_binding.resolve_material_selection_binding(
                research_id, input_binding_ref
            )
        if "material_recovery" in normalized:
            if binding is None:
                raise ValueError("mock material recovery binding is absent")
            refs = []
            for bundle in binding["material_bundles"]:
                material = bundle["research_material"]
                revision = bundle["material_revision"]
                refs.append(
                    {
                        "material_id": material["material_id"],
                        "revision_id": revision["revision_id"],
                        "input_artifact_id": revision["artifact_ref"]["sha256"],
                    }
                )
            context = binding["binding_context"]
            return self.record_material_recovery_decision(
                research_id,
                normalized,
                effective_material_revision_refs=refs,
                research_start=context.get("research_start"),
            )
        return self.supply_material_or_decision(research_id, normalized)

    def _load_state(self, research_id: str) -> dict[str, Any]:
        if research_id in self.states:
            return self.states[research_id]
        target = (
            self.root
            / "projects"
            / research_id
            / "mock"
            / "project-record-projection.json"
        )
        if not target.is_file():
            raise KeyError("unknown mock research")
        value = json.loads(target.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("contract") != MOCK_CONTRACT
            or value.get("research_id") != research_id
            or not isinstance(value.get("state"), dict)
        ):
            raise ValueError("mock Project Record projection is invalid")
        self.states[research_id] = dict(value["state"])
        return self.states[research_id]

    def _ref(self, research_id: str) -> dict[str, str]:
        relative = (
            Path("projects")
            / research_id
            / "mock"
            / "project-record-projection.json"
        )
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(
                {
                    "contract": MOCK_CONTRACT,
                    "research_id": research_id,
                    "state": self.states[research_id],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        target.write_bytes(payload)
        return {
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "role": "project_record_projection",
        }

    def start_research(self, request: Mapping[str, Any]) -> dict[str, Any]:
        research_id = str(request["research_id"])
        mode = self.modes.get(research_id, "complete")
        try:
            self._load_state(research_id)
        except KeyError:
            self.states[research_id] = {"mode": mode, "supplied": False}
        return self.read_status(research_id)

    def start_research_with_materials(
        self,
        request: Mapping[str, Any],
        material_bundles: object,
    ) -> dict[str, Any]:
        """Exercise the real material resolver while keeping the dev runtime inert."""

        research_id = str(request["research_id"])
        if not isinstance(material_bundles, list) or not material_bundles:
            raise ValueError("mock material bundles are absent")
        accepted_revisions: list[dict[str, Any]] = []
        for bundle in material_bundles:
            if not isinstance(bundle, Mapping) or bundle.get("contract") != (
                "lca_research_kernel.source_bound_material_revision/v1"
            ):
                raise ValueError("mock material bundle is invalid")
            material = bundle.get("research_material")
            revision = bundle.get("material_revision")
            receipt = bundle.get("processing_receipt")
            if (
                not isinstance(material, Mapping)
                or material.get("project_id") != research_id
                or not isinstance(revision, Mapping)
                or revision.get("material_id") != material.get("material_id")
                or not isinstance(receipt, Mapping)
                or receipt.get("candidate_only") is not True
                or receipt.get("status") not in {"ready", "partial", "needs_user"}
            ):
                raise ValueError("mock material bundle is not source-bound")
            accepted_revisions.append(
                {
                    "material_id": material["material_id"],
                    "revision_id": revision["revision_id"],
                    "input_sha256": receipt["input_sha256"],
                    "locator_ids": [
                        locator["locator_id"] for locator in bundle.get("locators", [])
                    ],
                }
            )
        status = self.start_research(request)
        self.states[research_id]["material_use"] = accepted_revisions
        return {**status, "project_record_ref": self._ref(research_id)}

    def supply_material_or_decision(
        self, research_id: str, external_input: Mapping[str, Any]
    ) -> dict[str, Any]:
        state = self._load_state(research_id)
        state["supplied"] = True
        state["mode"] = "complete"
        return self.read_status(research_id)

    def record_material_recovery_decision(
        self,
        research_id: str,
        external_input: Mapping[str, Any],
        *,
        effective_material_revision_refs: list[Mapping[str, Any]],
        research_start: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Mirror the audited decision shape without claiming real Assurance."""

        try:
            state = self._load_state(research_id)
        except KeyError:
            if not isinstance(research_start, Mapping):
                raise
            self.start_research(research_start)
            state = self._load_state(research_id)
        payload = external_input.get("payload")
        recovery = external_input.get("material_recovery")
        if not isinstance(payload, Mapping) or not isinstance(recovery, Mapping):
            raise ValueError("mock material recovery input is invalid")
        decision = (
            payload.get("decision")
            if external_input.get("kind") == "decision"
            else "replace_failed_materials"
        )
        observed = {
            "input_id": external_input.get("input_id"),
            "decision": decision,
            "failed_materials": deepcopy(recovery.get("failed_materials")),
            "effective_material_revision_refs": deepcopy(
                effective_material_revision_refs
            ),
            "claim_ceiling": "no_effect_mock_only",
        }
        decisions = state.setdefault("material_recovery_decisions", [])
        prior = next(
            (
                item
                for item in decisions
                if item.get("input_id") == observed["input_id"]
            ),
            None,
        )
        if prior is None:
            decisions.append(observed)
        elif prior != observed:
            raise ValueError("mock material recovery decision drifted")
        return {
            "event_type": "mock.material_recovery_decision_recorded",
            "ref": self._ref(research_id),
        }

    def read_status(self, research_id: str) -> dict[str, Any]:
        state = self._load_state(research_id)
        mode = state["mode"]
        if mode == "waiting":
            return {
                "contract": "lca_research_kernel.research_status_view/v1",
                "research_id": research_id,
                "status": "awaiting_user_data",
                "boundary": "awaiting_user_data",
                "interaction_kind": "factual_data_availability",
                "authorization_required": False,
                "question": "请补充缺失的事实材料。",
                "result_status": "not_yet_accepted",
                "project_record_sha256": "0" * 64,
                "project_record_ref": self._ref(research_id),
            }
        if mode == "failed":
            return {
                "contract": "lca_research_kernel.research_status_view/v1",
                "research_id": research_id,
                "status": "failed",
                "boundary": "runtime_failure",
                "failure_code": "mock_runtime_failure",
                "retryable": False,
                "result_status": "not_yet_accepted",
                "project_record_sha256": "0" * 64,
                "project_record_ref": self._ref(research_id),
            }
        return {
            "contract": "lca_research_kernel.research_status_view/v1",
            "research_id": research_id,
            "status": "completed",
            "boundary": "result_accepted",
            "result_status": "accepted",
            "project_record_sha256": "0" * 64,
            "project_record_ref": self._ref(research_id),
        }

    def read_result(self, research_id: str) -> dict[str, Any]:
        state = self._load_state(research_id)
        mode = state["mode"]
        if mode not in {"complete", "bounded"}:
            return {
                "contract": "lca_research_kernel.research_result_view/v1",
                "research_id": research_id,
                "status": "result_not_yet_accepted",
                "result": None,
                "project_record_ref": self._ref(research_id),
            }
        return {
            "contract": "lca_research_kernel.research_result_view/v1",
            "research_id": research_id,
            "status": "accepted",
            "result": {
                "status": "candidate_only",
                "claim_ceiling": (
                    "bounded_missing_data_conclusion"
                    if mode == "bounded"
                    else "candidate_only"
                ),
                "summary": (
                    "受限结果已接纳" if mode == "bounded" else "候选结果已接纳"
                ),
            },
            "project_record_ref": self._ref(research_id),
        }


def create_mock_app(
    data_root: Path,
    *,
    bearer_token: str = "integration-token",
    modes: Mapping[str, str] | None = None,
    auto_worker: bool = False,
):
    interface = NoEffectMockProductInterface(data_root, modes=modes)
    runtime = ProductInterfaceRuntime(interface, data_root=data_root)
    app = create_app(
        ServiceConfig(
            bearer_token=bearer_token,
            data_root=data_root,
            allowed_attachment_hosts=("assets.example.invalid",),
        ),
        runtime=runtime,
        worker_supervisor_factory=(
            lambda store, material_store: NoEffectWorkerSupervisor(
                store,
                runtime=runtime,
                data_root=data_root,
                material_store=material_store,
            )
            if auto_worker
            else None
        ),
    )
    app.state.mock_interface = interface
    app.state.mock_contract = MOCK_CONTRACT

    @app.get("/__dev/no-effect-receipt", include_in_schema=False)
    def no_effect_receipt(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if authorization != f"Bearer {bearer_token}":
            raise HTTPException(status_code=401, detail="invalid development token")
        return {
            "contract": "lca_research_service.no_effect_observation/v1",
            "effects": dict(interface.effects),
        }

    @app.get("/__dev/material-fixtures/{scenario}", include_in_schema=False)
    def material_fixture(
        scenario: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if authorization != f"Bearer {bearer_token}":
            raise HTTPException(status_code=401, detail="invalid development token")
        manifest = json.loads(_MATERIAL_FIXTURE_MANIFEST.read_text(encoding="utf-8"))
        if (
            manifest.get("contract")
            != "lca_research_service.material_fixture_manifest/v1"
            or manifest.get("effects")
            != {
                "external_writes": 0,
                "model_calls": 0,
                "network_calls": 0,
                "scientific_results": 0,
                "tool_calls": 0,
            }
            or not isinstance(manifest.get("scenarios"), Mapping)
        ):
            raise HTTPException(status_code=503, detail="material fixtures unavailable")
        fixture = manifest["scenarios"].get(scenario)
        if not isinstance(fixture, Mapping):
            raise HTTPException(status_code=404, detail="unknown material fixture")
        return {
            "contract": "lca_research_service.material_fixture/v1",
            "scenario": scenario,
            "candidate_only": True,
            "fixture": deepcopy(dict(fixture)),
            "replay": replay_material_fixture(fixture),
            "effects": deepcopy(manifest["effects"]),
        }
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--bearer-token", default="integration-token")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    import uvicorn

    uvicorn.run(
        create_mock_app(
            args.data_root,
            bearer_token=args.bearer_token,
            auto_worker=True,
        ),
        host=args.host,
        port=args.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
