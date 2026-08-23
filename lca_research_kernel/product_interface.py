"""Single product entry for starting, supplying, reading and delivering research."""

from __future__ import annotations

import hashlib
import re
import json
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from .agent_bootstrap import (
    ADOPTED_STARTUP_MANIFEST_SHA256,
    AgentBootstrap,
    validate_startup_context,
)
from .project_record import ProjectRecord, canonical_sha256
from .research_orchestrator import ResearchOrchestrator
from .material_gap_closure import MaterialGapClosure
from .run_trace import RunTrace, canonical_json_bytes
from .run_control import DEFAULT_RUN_BUDGET, RunController
from .execution_environment import ProjectExecutionEnvironment
from .answer_timing import AnswerTiming
from .artifact_workers import ArtifactWorkerCoordinator, ModelArtifactWorkerRunner
from .material_sources import validate_source_bound_material_revision
from .material_resolution import MaterialResolutionBinding
from .material_turn_compiler import MaterialTurnCompiler
from .material_turn_executor import project_material_candidate_claims


RESEARCH_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STARTUP_MANIFEST = (
    REPOSITORY_ROOT
    / "lca_research_kernel/contracts/agent-startup-manifest-v2.json"
)
DEFAULT_EXECUTION_POLICY = (
    REPOSITORY_ROOT
    / "lca_research_kernel/contracts/project-execution-policy.json"
)
START_FIELDS = {
    "contract",
    "research_id",
    "title",
    "objective",
    "source_refs",
    "received_at",
}
SUPPLY_FIELDS = {
    "contract",
    "input_id",
    "kind",
    "payload",
    "source_refs",
    "received_at",
}
TURN_FIELDS = {
    "contract",
    "input_id",
    "text",
    "source_refs",
    "received_at",
}


class ProductInterface:
    def __init__(
        self,
        root: Path,
        *,
        model_gateway: object,
        capability_gateway: object = None,
        assurance: object,
        clock: Callable[[], str],
        startup_context: Mapping[str, object] = None,
        run_id: str = None,
        run_budget: Mapping[str, object] = None,
        monotonic: Callable[[], float] = time.monotonic,
        semantic_context: object = None,
        enable_artifact_workers: bool = False,
        verified_content_resolver: Callable[
            [str, str, Mapping[str, object]], bytes
        ]
        | None = None,
        material_revision_resolver: Callable[
            [str, list[Mapping[str, object]]], list[Mapping[str, object]]
        ]
        | None = None,
        material_resolution_binding: MaterialResolutionBinding | None = None,
    ):
        self.root = Path(root)
        self.model_gateway = model_gateway
        self.capability_gateway = capability_gateway
        self.assurance = assurance
        self.clock = clock
        self.monotonic = monotonic
        self.semantic_context = semantic_context
        self.material_turn_compiler = MaterialTurnCompiler(
            REPOSITORY_ROOT,
            clock=clock,
            semantic_context=semantic_context,
        )
        if not isinstance(enable_artifact_workers, bool):
            raise ValueError("artifact worker runtime flag is invalid")
        self.enable_artifact_workers = enable_artifact_workers
        if material_resolution_binding is not None and not isinstance(
            material_resolution_binding, MaterialResolutionBinding
        ):
            raise ValueError("material resolution binding is invalid")
        self.material_resolution_binding = material_resolution_binding or (
            MaterialResolutionBinding(
                verified_content_resolver=verified_content_resolver,
                material_revision_resolver=material_revision_resolver,
            )
        )
        self._verified_content_resolver = (
            self.material_resolution_binding.resolve_verified_content
        )
        self._material_revision_resolver = (
            self.material_resolution_binding.resolve_revisions
        )
        self.run_started_at = self.clock()
        loaded_startup = (
            AgentBootstrap.load(
                REPOSITORY_ROOT,
                DEFAULT_STARTUP_MANIFEST,
                expected_manifest_sha256=ADOPTED_STARTUP_MANIFEST_SHA256,
            )
            if startup_context is None
            else startup_context
        )
        self.startup_context = validate_startup_context(loaded_startup)
        if (
            self.startup_context.get("manifest_sha256")
            != ADOPTED_STARTUP_MANIFEST_SHA256
        ):
            raise ValueError(
                "startup context does not match the adopted startup manifest"
            )
        self.run_id_was_generated = run_id is None
        self._default_run_id = run_id or f"agent-runtime-{uuid.uuid4()}"
        self._run_identity = threading.local()
        if not isinstance(self._default_run_id, str) or not self._default_run_id.strip():
            raise ValueError("Product Interface run_id is required")
        self.run_budget = deepcopy(
            dict(DEFAULT_RUN_BUDGET if run_budget is None else run_budget)
        )
        self.material_gap_closure = MaterialGapClosure()
        self._answer_timings: dict[tuple[str, str], AnswerTiming] = {}
        self._active_lock = threading.RLock()
        self._active_research_ids: set[str] = set()
        try:
            execution_policy = json.loads(
                DEFAULT_EXECUTION_POLICY.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("project execution policy is unavailable") from error
        if not isinstance(execution_policy, Mapping):
            raise ValueError("project execution policy must be an object")
        self.execution_policy = deepcopy(dict(execution_policy))

    @property
    def run_id(self) -> str:
        return getattr(self._run_identity, "value", self._default_run_id)

    @contextmanager
    def use_run_id(self, run_id: str):
        """Bind one durable request trace identity to the current execution."""

        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("Product Interface run_id is required")
        prior = getattr(self._run_identity, "value", None)
        self._run_identity.value = run_id
        try:
            yield
        finally:
            if prior is None:
                del self._run_identity.value
            else:
                self._run_identity.value = prior

    def _resolve_material_revision_refs(
        self,
        research_id: str,
        revision_refs: object,
    ) -> list[dict]:
        if (
            not isinstance(revision_refs, list)
            or not revision_refs
            or any(not isinstance(item, Mapping) for item in revision_refs)
        ):
            raise ValueError("material revision references are invalid")
        resolver = self._material_revision_resolver
        if not callable(resolver):
            raise ValueError("material revision resolution is unavailable")
        resolved = resolver(
            research_id,
            [deepcopy(dict(item)) for item in revision_refs],
        )
        return self._validate_material_bundles(
            resolved,
            project_id=research_id,
        )

    def submit_research(
        self,
        request: Mapping[str, object],
    ) -> dict:
        """Submit research through the minimal product surface.

        Callers may supply only a content-bound opaque input binding.  Revision,
        processing, evidence-unit and locator protocols remain behind this port.
        """

        normalized_request = deepcopy(dict(request))
        input_binding_ref = normalized_request.pop("input_binding_ref", None)
        if input_binding_ref is None:
            return self.start_research(normalized_request)
        research_id = normalized_request.get("research_id")
        if not isinstance(research_id, str) or not research_id.strip():
            raise ValueError("research_id is required")
        bundles = self._validate_material_bundles(
            self.material_resolution_binding.resolve_material_selection(
                research_id,
                input_binding_ref,
            ),
            project_id=research_id,
        )
        return self.start_research_with_materials(normalized_request, bundles)

    def _resolved_input_binding(
        self,
        research_id: str,
        external_input: Mapping[str, object],
    ) -> tuple[dict[str, object], list[dict] | None, dict[str, object]]:
        normalized_input = deepcopy(dict(external_input))
        input_binding_ref = normalized_input.pop("input_binding_ref", None)
        if input_binding_ref is None:
            return normalized_input, None, {}
        binding = self.material_resolution_binding.resolve_material_selection_binding(
            research_id,
            input_binding_ref,
        )
        raw_bundles = binding["material_bundles"]
        bundles = (
            self._validate_material_bundles(
                raw_bundles,
                project_id=research_id,
            )
            if raw_bundles
            else []
        )
        context = binding.get("binding_context")
        if not isinstance(context, Mapping):
            raise ValueError("input binding context is invalid")
        return normalized_input, bundles, deepcopy(dict(context))

    @staticmethod
    def _effective_revision_refs(material_bundles: list[dict]) -> list[dict]:
        refs = []
        for bundle in material_bundles:
            material = bundle.get("research_material")
            revision = bundle.get("material_revision")
            artifact = (
                revision.get("artifact_ref")
                if isinstance(revision, Mapping)
                else None
            )
            if (
                not isinstance(material, Mapping)
                or not isinstance(revision, Mapping)
                or not isinstance(artifact, Mapping)
                or not isinstance(material.get("material_id"), str)
                or not isinstance(revision.get("revision_id"), str)
                or not isinstance(artifact.get("sha256"), str)
            ):
                raise ValueError("material input binding is invalid")
            refs.append(
                {
                    "material_id": material["material_id"],
                    "revision_id": revision["revision_id"],
                    "input_artifact_id": artifact["sha256"],
                }
            )
        return refs

    def submit_input(
        self,
        research_id: str,
        external_input: Mapping[str, object],
    ) -> dict:
        """Continue through one opaque input binding or a normal user input."""

        normalized_input, bundles, binding_context = self._resolved_input_binding(
            research_id, external_input
        )
        if "material_recovery" in normalized_input:
            research_start = binding_context.get("research_start")
            if research_start is not None and not isinstance(
                research_start, Mapping
            ):
                raise ValueError("material recovery start binding is invalid")
            return self.record_material_recovery_decision(
                research_id,
                normalized_input,
                effective_material_revision_refs=(
                    self._effective_revision_refs(bundles or [])
                ),
                research_start=research_start,
            )
        if normalized_input.get("contract") == (
            "lca_research_kernel.research_user_turn/v1"
        ):
            if bundles is None:
                return self.continue_research(research_id, normalized_input)
            return self.continue_research(
                research_id, normalized_input, material_bundles=bundles
            )
        if bundles is None:
            return self.supply_material_or_decision(research_id, normalized_input)
        if normalized_input.get("kind") != "material":
            raise ValueError("input binding requires material input")
        return self.supply_material_revisions(
            research_id,
            normalized_input,
            bundles,
        )

    def start_research(self, request: Mapping[str, object]) -> dict:
        normalized = self._validate_start(request)
        research_id = normalized["research_id"]
        record = ProjectRecord.open_or_create_project(
            self._workspace(research_id),
            project_id=research_id,
            title=normalized["title"],
            objective=normalized["objective"],
            created_at=normalized["received_at"],
            verified_content_resolver=self._verified_content_resolver,
        )
        self._record_startup(record)
        answer_timing = self._answer_timing(
            record,
            input_sha256=canonical_sha256(normalized),
        )
        answer_timing.enter_request()
        orchestrator = self._orchestrator(record, answer_timing=answer_timing)
        with self._active_lock:
            self._active_research_ids.add(research_id)
        try:
            boundary = orchestrator.resume(
                research_id,
                {
                    "contract": "lca_research_kernel.research_intake/v1",
                    "intake_id": f"start-{research_id}",
                    "research_goal": normalized["objective"],
                    "source_refs": deepcopy(normalized["source_refs"]),
                    "received_at": normalized["received_at"],
                },
            )
        finally:
            with self._active_lock:
                self._active_research_ids.discard(research_id)
        return self._status_view(record, boundary)

    def start_research_with_materials(
        self,
        request: Mapping[str, object],
        material_bundles: list[Mapping[str, object]],
    ) -> dict:
        """Submit one versioned material request to the Research Orchestrator."""

        normalized = self._validate_start(request)
        research_id = normalized["research_id"]
        bundles = self._validate_material_bundles(
            material_bundles, project_id=research_id
        )
        material_run_id = self._material_request_run_id(
            normalized=normalized,
            bundles=bundles,
        )
        record = ProjectRecord.open_or_create_project(
            self._workspace(research_id),
            project_id=research_id,
            title=normalized["title"],
            objective=normalized["objective"],
            created_at=normalized["received_at"],
            verified_content_resolver=self._verified_content_resolver,
        )
        self._record_startup(record, run_id=material_run_id)
        answer_timing = self._answer_timing(
            record,
            input_sha256=canonical_sha256(normalized),
            run_id=material_run_id,
        )
        answer_timing.enter_request()
        boundary = self._orchestrator(
            record,
            answer_timing=answer_timing,
            run_id=material_run_id,
        ).advance_material_research(
            normalized=normalized,
            material_bundles=bundles,
            turn_compiler=self.material_turn_compiler,
            run_budget=self.run_budget,
        )
        return self._status_view(record, boundary)

    def _material_request_run_id(
        self,
        *,
        normalized: Mapping[str, object],
        bundles: list[Mapping[str, object]],
    ) -> str:
        """Choose one durable runtime identity before orchestration begins."""

        bound_run_id = getattr(self._run_identity, "value", None)
        if isinstance(bound_run_id, str) and bound_run_id:
            return bound_run_id
        if not self.run_id_was_generated:
            return self.run_id
        return "material-answer-" + canonical_sha256(
            {
                "contract": "lca_research_kernel.material_request_run_identity/v1",
                "request_sha256": canonical_sha256(normalized),
                "revision_ids": sorted(
                    bundle["material_revision"]["revision_id"]
                    for bundle in bundles
                ),
            }
        )

    def _ensure_material_active_turn(
        self,
        record: ProjectRecord,
        normalized: Mapping[str, object],
    ) -> str:
        return self._orchestrator(
            record, initialize_answer_timing=False
        ).ensure_material_active_turn(
            normalized,
            turn_compiler=self.material_turn_compiler,
            run_budget=self.run_budget,
        )

    def supply_material_revisions(
        self,
        research_id: str,
        external_input: Mapping[str, object],
        material_bundles: list[Mapping[str, object]],
    ) -> dict:
        """Accept resolved material revisions through the same material seam."""

        supplied = self._validate_supply(external_input)
        if supplied["kind"] != "material":
            raise ValueError("material revision supply kind is unsupported")
        record = self._open_record(research_id)
        view = record.read_current_project_view()
        request = {
            "contract": "lca_research_kernel.research_start/v1",
            "research_id": research_id,
            "title": view["title"],
            "objective": view["objective"],
            "source_refs": deepcopy(supplied["source_refs"]),
            "received_at": supplied["received_at"],
        }
        return self.start_research_with_materials(request, material_bundles)

    def supply_material_or_decision(
        self, research_id: str, external_input: Mapping[str, object]
    ) -> dict:
        record = self._open_record(research_id)
        self._record_startup(record)
        supplied = self._validate_supply(external_input)
        boundary = self._orchestrator(record).accept_product_input(
            research_id=research_id,
            supplied=supplied,
        )
        return self._status_view(record, boundary)

    def record_material_recovery_decision(
        self,
        research_id: str,
        external_input: Mapping[str, object],
        *,
        effective_material_revision_refs: list[Mapping[str, object]],
        research_start: Mapping[str, object] | None = None,
    ) -> dict:
        """Accept a user recovery choice before the service changes evidence scope."""

        if set(external_input) != SUPPLY_FIELDS | {"material_recovery"}:
            raise ValueError("material recovery input fields are unsupported")
        supplied = self._validate_supply(
            {key: external_input[key] for key in SUPPLY_FIELDS}
        )
        recovery = external_input.get("material_recovery")
        if (
            not isinstance(recovery, Mapping)
            or recovery.get("contract")
            != "lca_research_service.material_processing_recovery/v1"
            or not isinstance(recovery.get("failed_materials"), list)
            or not recovery["failed_materials"]
            or not isinstance(recovery.get("allowed_decisions"), list)
        ):
            raise ValueError("material recovery boundary is invalid")
        normalized_refs: list[dict[str, object]] = []
        for reference in effective_material_revision_refs:
            if (
                not isinstance(reference, Mapping)
                or set(reference)
                != {"material_id", "revision_id", "input_artifact_id"}
                or not all(
                    isinstance(reference.get(key), str) and reference.get(key)
                    for key in ("material_id", "revision_id", "input_artifact_id")
                )
                or SHA256_RE.fullmatch(str(reference["input_artifact_id"])) is None
            ):
                raise ValueError("material recovery effective revision is invalid")
            normalized_refs.append(deepcopy(dict(reference)))
        try:
            record = self._open_record(research_id)
        except ValueError:
            if research_start is None:
                raise
            normalized_start = self._validate_start(research_start)
            if normalized_start["research_id"] != research_id:
                raise ValueError("material recovery research identity drifted")
            record = ProjectRecord.open_or_create_project(
                self._workspace(research_id),
                project_id=research_id,
                title=normalized_start["title"],
                objective=normalized_start["objective"],
                created_at=normalized_start["received_at"],
                verified_content_resolver=self._verified_content_resolver,
            )
        self._record_startup(record)
        return self._orchestrator(record).accept_material_recovery(
            supplied=supplied,
            recovery=recovery,
            effective_material_revision_refs=normalized_refs,
        )

    def continue_research(
        self,
        research_id: str,
        user_turn: Mapping[str, object],
        material_bundles: list[Mapping[str, object]] | None = None,
        *,
        material_revision_refs: object = None,
    ) -> dict:
        """Accept one content-bound user turn and resume the same Project Record."""

        record = self._open_record(research_id)
        normalized = self._validate_user_turn(user_turn)
        if material_bundles is not None and material_revision_refs is not None:
            raise ValueError("material input has multiple resolution authorities")
        if material_revision_refs is None and material_bundles is None:
            current_refs = self.current_material_revision_refs(research_id)
            if current_refs and callable(self._material_revision_resolver):
                material_revision_refs = current_refs
        bundles = (
            self._resolve_material_revision_refs(
                research_id,
                material_revision_refs,
            )
            if material_revision_refs is not None
            else (
                self._validate_material_bundles(
                    material_bundles,
                    project_id=research_id,
                )
                if material_bundles
                else []
            )
        )
        turn_sha256 = canonical_sha256(normalized)
        turn_run_id = f"{self.run_id}-turn-{turn_sha256[:12]}"
        self._record_startup(record, run_id=turn_run_id)
        answer_timing = self._answer_timing(
            record,
            input_sha256=turn_sha256,
            run_id=turn_run_id,
        )
        boundary = self._orchestrator(
            record,
            answer_timing=answer_timing,
            run_id=turn_run_id,
        ).advance_product_continuation(
            research_id=research_id,
            normalized=normalized,
            material_bundles=bundles,
            turn_sha256=turn_sha256,
            turn_run_id=turn_run_id,
            turn_compiler=self.material_turn_compiler,
            run_budget=self.run_budget,
        )
        return self._status_view(record, boundary)

    def resume_research(self, research_id: str) -> dict:
        """Advance accepted project state without replaying its original intake."""

        record = self._open_record(research_id)
        self._record_startup(record)
        answer_timing = self._answer_timing(record)
        answer_timing.enter_request()
        orchestrator = self._orchestrator(
            record,
            answer_timing=answer_timing,
        )
        with self._active_lock:
            self._active_research_ids.add(research_id)
        try:
            boundary = orchestrator.run_until_boundary(research_id)
        finally:
            with self._active_lock:
                self._active_research_ids.discard(research_id)
        return self._status_view(record, boundary)

    def read_status(self, research_id: str) -> dict:
        record = self._open_record(research_id)
        with self._active_lock:
            active = research_id in self._active_research_ids
        if active:
            return self._status_view(
                record,
                {"boundary": "candidate_preview_available"},
            )
        self._record_startup(record)
        boundary = self._observed_boundary(record)
        return self._status_view(record, boundary)

    def read_result(self, research_id: str) -> dict:
        record = self._open_record(research_id)
        self._record_startup(record)
        view = record.read_current_project_view()
        if self._has_unresolved_user_turn(view):
            effect_receipt = self._effect_receipt(record)
            return {
                "contract": "lca_research_kernel.research_result_view/v1",
                "research_id": research_id,
                "status": "result_not_yet_accepted",
                "result": None,
                "project_record_ref": self._project_record_ref(record),
                "run_trace_ref": deepcopy(
                    effect_receipt["registered_run_trace_refs"][0]
                ),
                "effect_receipt": effect_receipt,
            }
        if view.get("result") is None:
            effect_receipt = self._effect_receipt(record)
            return {
                "contract": "lca_research_kernel.research_result_view/v1",
                "research_id": research_id,
                "status": "result_not_yet_accepted",
                "result": None,
                "project_record_ref": self._project_record_ref(record),
                "run_trace_ref": deepcopy(
                    effect_receipt["registered_run_trace_refs"][0]
                ),
                "effect_receipt": effect_receipt,
            }
        result_run_id = view["result"].get("interaction_run_id")
        timing = self._answer_timing(
            record,
            run_id=(
                result_run_id
                if isinstance(result_run_id, str) and result_run_id
                else self.run_id
            ),
        )
        result_sha256 = canonical_sha256(view["result"])
        effect_receipt = self._effect_receipt(record)
        timing.mark_user_delivery(result_sha256)
        return {
            "contract": "lca_research_kernel.research_result_view/v1",
            "research_id": research_id,
            "status": "accepted",
            "result": deepcopy(view["result"]),
            "delivery": deepcopy(view["result"].get("human_delivery")),
            "project_record_ref": self._project_record_ref(record),
            "answer_timing_receipt": timing.receipt(),
            "execution_receipt": self._public_execution_receipt(
                record,
                answer_timing=timing,
                effect_receipt=effect_receipt,
            ),
            "run_trace_ref": deepcopy(
                effect_receipt["registered_run_trace_refs"][0]
            ),
            "effect_receipt": effect_receipt,
        }

    def _validate_material_bundles(
        self,
        material_bundles: object,
        *,
        project_id: str,
    ) -> list[dict]:
        if not isinstance(material_bundles, list) or not material_bundles:
            raise ValueError("source-bound material revisions are absent")
        normalized = [
            validate_source_bound_material_revision(value)
            for value in material_bundles
        ]
        material_ids: set[str] = set()
        revision_ids: set[str] = set()
        locator_ids: set[str] = set()
        for bundle in normalized:
            material = bundle["research_material"]
            revision = bundle["material_revision"]
            if material["project_id"] != project_id:
                raise ValueError("source-bound material belongs to another project")
            if material["material_id"] in material_ids:
                raise ValueError("source-bound material identity is duplicated")
            if revision["revision_id"] in revision_ids:
                raise ValueError("source-bound material revision is duplicated")
            material_ids.add(material["material_id"])
            revision_ids.add(revision["revision_id"])
            for locator in bundle["locators"]:
                if locator["locator_id"] in locator_ids:
                    raise ValueError("source-bound evidence locator is duplicated")
                locator_ids.add(locator["locator_id"])
        return normalized

    def current_material_revision_refs(self, research_id: str) -> list[dict]:
        """Project-Record-derived identities for resolving current material bytes."""

        record = self._open_record(research_id)
        view = record.read_current_project_view()
        materials = view.get("materials") or {}
        revisions = view.get("material_revisions") or {}
        if not isinstance(materials, Mapping) or not isinstance(revisions, Mapping):
            raise ValueError("accepted material state is invalid")
        references = []
        for material_id, material in sorted(materials.items()):
            if (
                not isinstance(material, Mapping)
                or material.get("deleted_at") is not None
                or not isinstance(material.get("current_revision_id"), str)
            ):
                continue
            revision_id = material["current_revision_id"]
            revision = revisions.get(revision_id)
            artifact_ref = (
                revision.get("artifact_ref")
                if isinstance(revision, Mapping)
                else None
            )
            artifact_id = (
                artifact_ref.get("sha256")
                if isinstance(artifact_ref, Mapping)
                else None
            )
            if (
                not isinstance(artifact_id, str)
                or revision.get("material_id") != material_id
            ):
                raise ValueError("accepted material revision identity is invalid")
            references.append(
                {
                    "material_id": material_id,
                    "revision_id": revision_id,
                    "input_artifact_id": artifact_id,
                }
            )
        return references

    def _accepted_material_locator_index(
        self,
        record: ProjectRecord,
        *,
        bundles: list[Mapping[str, object]],
    ) -> dict[str, dict] | None:
        return self._orchestrator(
            record, initialize_answer_timing=False
        ).accepted_material_locator_index(
            material_bundles=bundles,
            turn_compiler=self.material_turn_compiler,
            run_budget=self.run_budget,
        )

    @staticmethod
    def _project_material_candidate_claims(
        candidate: Mapping[str, object],
        *,
        locator_hashes: Mapping[str, str] | None = None,
        response_language: str | None = None,
        governed_knowledge: Sequence[Mapping[str, object]] = (),
    ) -> tuple[dict, bool]:
        return project_material_candidate_claims(
            candidate,
            locator_hashes=locator_hashes,
            response_language=response_language,
            governed_knowledge=governed_knowledge,
        )

    def _evaluate_material_model_candidate(
        self,
        record: ProjectRecord,
        *,
        normalized: Mapping[str, object],
        bundles: list[Mapping[str, object]],
        use_decision: Mapping[str, object],
        decision_event_sha256: str,
        answer_timing: AnswerTiming | None = None,
        run_id: str | None = None,
    ) -> dict:
        effective_run_id = run_id or self._material_request_run_id(
            normalized=normalized,
            bundles=bundles,
        )
        timing = answer_timing or self._answer_timing(
            record,
            input_sha256=canonical_sha256(normalized),
            run_id=effective_run_id,
        )
        return self._orchestrator(
            record,
            answer_timing=timing,
            run_id=effective_run_id,
        ).advance_material_turn(
            normalized=normalized,
            material_bundles=bundles,
            material_use_decision=use_decision,
            material_use_decision_event_sha256=decision_event_sha256,
            turn_compiler=self.material_turn_compiler,
        )


    def _accept_material_inputs(
        self,
        record: ProjectRecord,
        *,
        bundles: list[Mapping[str, object]],
        proposed_at: str,
    ) -> dict[str, dict]:
        return self._orchestrator(
            record, initialize_answer_timing=False
        ).accept_material_inputs(
            material_bundles=bundles,
            proposed_at=proposed_at,
            turn_compiler=self.material_turn_compiler,
            run_budget=self.run_budget,
        )

    def _prepare_material_use_decision(
        self,
        record: ProjectRecord,
        *,
        request: Mapping[str, object],
        bundles: list[Mapping[str, object]],
        locator_acceptance: Mapping[str, Mapping[str, object]],
        proposed_at: str,
    ) -> tuple[dict, str]:
        return self._orchestrator(
            record, initialize_answer_timing=False
        ).prepare_material_use_decision(
            normalized=request,
            material_bundles=bundles,
            locator_acceptance=locator_acceptance,
            proposed_at=proposed_at,
            turn_compiler=self.material_turn_compiler,
            run_budget=self.run_budget,
        )

    def _workspace(self, research_id: str) -> Path:
        if not isinstance(research_id, str) or RESEARCH_ID_RE.fullmatch(research_id) is None:
            raise ValueError("research_id is invalid or unsafe")
        return self.root / "projects" / research_id

    def _open_record(self, research_id: str) -> ProjectRecord:
        workspace = self._workspace(research_id)
        try:
            return ProjectRecord.open_or_create_project(
                workspace,
                verified_content_resolver=self._verified_content_resolver,
            )
        except ValueError as error:
            raise ValueError(f"research_id has no Project Record: {research_id}") from error

    def _orchestrator(
        self,
        record: ProjectRecord,
        *,
        answer_timing: AnswerTiming = None,
        run_id: str = None,
        initialize_answer_timing: bool = True,
    ) -> ResearchOrchestrator:
        effective_run_id = run_id or self.run_id
        trace = RunTrace(
            record.workspace / "run-traces" / f"{effective_run_id}.jsonl",
            run_id=effective_run_id,
        )
        run_controller = RunController.restore(
            self.run_budget,
            run_id=effective_run_id,
            trace=trace,
            monotonic=self.monotonic,
        )
        execution_environment = ProjectExecutionEnvironment.create(
            self.root,
            str(record.read_current_project_view()["project_id"]),
            self.execution_policy,
        )
        capability_gateway = self.capability_gateway
        if capability_gateway is not None and hasattr(
            capability_gateway, "with_execution_environment"
        ):
            capability_gateway = capability_gateway.with_execution_environment(
                execution_environment
            )
        model_gateway = self.model_gateway
        if hasattr(model_gateway, "with_execution_environment"):
            model_gateway = model_gateway.with_execution_environment(
                execution_environment
            )
        worker_coordinator = (
            ArtifactWorkerCoordinator(
                runner=ModelArtifactWorkerRunner(model_gateway, trace=trace),
                clock=self.clock,
                max_workers=max(
                    1, min(4, int(self.run_budget["max_model_calls"]))
                ),
            )
            if self.enable_artifact_workers
            else None
        )
        return ResearchOrchestrator(
            record,
            model_gateway=model_gateway,
            capability_gateway=capability_gateway,
            assurance=self.assurance,
            clock=self.clock,
            material_gap_closure=self.material_gap_closure,
            run_trace=trace,
            run_controller=run_controller,
            execution_environment=execution_environment,
            answer_timing=(
                answer_timing
                or self._answer_timing(
                    record, trace=trace, run_id=effective_run_id
                )
                if initialize_answer_timing
                else None
            ),
            semantic_context=self.semantic_context,
            worker_coordinator=worker_coordinator,
        )

    def _answer_timing(
        self,
        record: ProjectRecord,
        *,
        input_sha256: str = None,
        trace: RunTrace = None,
        run_id: str = None,
    ) -> AnswerTiming:
        project_id = str(record.read_current_project_view()["project_id"])
        effective_run_id = run_id or self.run_id
        timing_key = (project_id, effective_run_id)
        prior = self._answer_timings.get(timing_key)
        if prior is not None:
            if input_sha256 is not None and prior.input_sha256 != input_sha256:
                raise ValueError("answer timing input identity drifted")
            return prior
        active_trace = trace or RunTrace(
            record.workspace / "run-traces" / f"{effective_run_id}.jsonl",
            run_id=effective_run_id,
        )
        if input_sha256 is None:
            requested = next(
                (
                    event
                    for event in active_trace.events()
                    if event["event_type"] == "research.run.answer.requested"
                ),
                None,
            )
            if requested is not None:
                input_sha256 = requested["payload"].get("input_sha256")
            else:
                view = record.read_current_project_view()
                input_sha256 = canonical_sha256(
                    {
                        "project_id": view["project_id"],
                        "objective": view["objective"],
                    }
                )
        timing = AnswerTiming(
            active_trace,
            run_id=effective_run_id,
            input_sha256=input_sha256,
            wall_clock=self.clock,
            monotonic=self.monotonic,
        )
        self._answer_timings[timing_key] = timing
        return timing

    def _record_startup(self, record: ProjectRecord, *, run_id: str = None) -> None:
        startup_context = validate_startup_context(self.startup_context)
        effective_run_id = run_id or self.run_id
        prior = (
            record.read_current_project_view()
            .get("agent_startups", {})
            .get(effective_run_id)
        )
        observed_at = self.run_started_at
        if (
            isinstance(prior, Mapping)
            and prior.get("context_sha256")
            == startup_context["context_sha256"]
        ):
            observed_at = prior.get("observed_at")
        if not isinstance(observed_at, str) or not observed_at.strip():
            raise ValueError("Agent startup recovery observation is invalid")
        record.record_agent_startup(
            startup_context,
            run_id=effective_run_id,
            observed_at=observed_at,
        )

    def _observed_boundary(self, record: ProjectRecord) -> dict:
        """Read the latest runtime boundary without advancing the research run."""

        view = record.read_current_project_view()
        unresolved_turn = self._unresolved_user_turn(view)
        if view.get("result") is not None and unresolved_turn is None:
            return {
                "contract": (
                    "lca_research_kernel.research_boundary/v1"
                ),
                "research_id": view["project_id"],
                "boundary": "research_result_accepted",
                "project_record_sha256": view["last_event_sha256"],
                "next_route": view["next_route"],
            }
        observed_run_id = (
            unresolved_turn.get("turn_run_id")
            if isinstance(unresolved_turn, Mapping)
            else self.run_id
        )
        if not isinstance(observed_run_id, str) or not observed_run_id:
            observed_run_id = self.run_id
        trace = RunTrace(
            record.workspace / "run-traces" / f"{observed_run_id}.jsonl",
            run_id=observed_run_id,
        )
        for event in reversed(trace.events()):
            if event.get("event_type") != "research.run.boundary.reached":
                continue
            payload = event.get("payload")
            boundary = (
                payload.get("boundary_view")
                if isinstance(payload, Mapping)
                else None
            )
            if (
                isinstance(boundary, Mapping)
                and boundary.get("research_id") == view["project_id"]
                and boundary.get("project_record_sha256")
                == view["last_event_sha256"]
            ):
                return deepcopy(dict(boundary))
        return {
            "contract": "lca_research_kernel.research_boundary/v1",
            "research_id": view["project_id"],
            "boundary": "status_observation_only",
            "project_record_sha256": view["last_event_sha256"],
            "next_route": view["next_route"],
        }

    @staticmethod
    def _unresolved_user_turn(view: Mapping[str, object]) -> Mapping[str, object] | None:
        facts = view.get("facts")
        turn = facts.get("active-user-turn") if isinstance(facts, Mapping) else None
        value = turn.get("value") if isinstance(turn, Mapping) else None
        if not isinstance(value, Mapping):
            return None
        turn_sha256 = value.get("turn_sha256")
        result = view.get("result")
        if (
            isinstance(turn_sha256, str)
            and (
                not isinstance(result, Mapping)
                or result.get("interaction_turn_sha256") != turn_sha256
            )
        ):
            return value
        return None

    @classmethod
    def _has_unresolved_user_turn(cls, view: Mapping[str, object]) -> bool:
        return cls._unresolved_user_turn(view) is not None

    def _validate_start(self, value: Mapping[str, object]) -> dict:
        if not isinstance(value, Mapping) or set(value) != START_FIELDS:
            raise ValueError("research start fields are incomplete or unsupported")
        if value.get("contract") != "lca_research_kernel.research_start/v1":
            raise ValueError("research start contract is unsupported")
        normalized = deepcopy(dict(value))
        self._workspace(normalized.get("research_id"))  # type: ignore[arg-type]
        for key in ("title", "objective", "received_at"):
            if not isinstance(normalized.get(key), str) or not normalized[key].strip():
                raise ValueError(f"research start {key} is absent")
        self._validate_refs(normalized.get("source_refs"))
        return normalized

    def _validate_supply(self, value: Mapping[str, object]) -> dict:
        if not isinstance(value, Mapping) or set(value) != SUPPLY_FIELDS:
            raise ValueError("research material or decision fields are unsupported")
        if value.get("contract") != (
            "lca_research_kernel.research_material_or_decision/v1"
        ):
            raise ValueError("research material or decision contract is unsupported")
        normalized = deepcopy(dict(value))
        if normalized.get("kind") not in {"material", "decision"}:
            raise ValueError("research input kind is unsupported")
        if not isinstance(normalized.get("input_id"), str) or not normalized[
            "input_id"
        ].strip():
            raise ValueError("research input_id is absent")
        if not isinstance(normalized.get("payload"), Mapping):
            raise ValueError("research input payload is absent")
        if not isinstance(normalized.get("received_at"), str) or not normalized[
            "received_at"
        ].strip():
            raise ValueError("research input received_at is absent")
        self._validate_refs(normalized.get("source_refs"))
        return normalized

    def _validate_user_turn(self, value: Mapping[str, object]) -> dict:
        if not isinstance(value, Mapping) or set(value) != TURN_FIELDS:
            raise ValueError("research user turn fields are unsupported")
        if value.get("contract") != "lca_research_kernel.research_user_turn/v1":
            raise ValueError("research user turn contract is unsupported")
        normalized = deepcopy(dict(value))
        for key in ("input_id", "text", "received_at"):
            if not isinstance(normalized.get(key), str) or not normalized[key].strip():
                raise ValueError(f"research user turn {key} is absent")
        self._validate_refs(normalized.get("source_refs"))
        return normalized

    def _validate_refs(self, refs: object) -> None:
        if not isinstance(refs, list) or not refs:
            raise ValueError("source_refs are absent")
        for reference in refs:
            if (
                not isinstance(reference, Mapping)
                or not isinstance(reference.get("path"), str)
                or not isinstance(reference.get("sha256"), str)
                or SHA256_RE.fullmatch(reference["sha256"]) is None
            ):
                raise ValueError("source_ref is invalid")

    def _status_view(self, record: ProjectRecord, boundary: Mapping[str, object]) -> dict:
        view = record.read_current_project_view()
        status = {
            "contract": "lca_research_kernel.research_status_view/v1",
            "research_id": view["project_id"],
            "status": view["status"],
            "boundary": boundary["boundary"],
            "fact_count": len(view["facts"]),
            "decision_count": len(view["decisions"]),
            "artifact_count": len(view["artifacts"]),
            "result_status": (
                "accepted"
                if view.get("result") is not None
                and not self._has_unresolved_user_turn(view)
                else "not_yet_accepted"
            ),
            "project_record_sha256": view["last_event_sha256"],
            "project_record_ref": self._project_record_ref(record),
        }
        timing = self._answer_timing(record)
        status["candidate_preview"] = timing.latest_preview()
        status["answer_timing_receipt"] = timing.receipt(
            include_user_delivery=False
        )
        effect_receipt = self._effect_receipt(record)
        status["run_trace_ref"] = deepcopy(
            effect_receipt["registered_run_trace_refs"][0]
        )
        status["effect_receipt"] = effect_receipt
        status["execution_receipt"] = self._public_execution_receipt(
            record,
            answer_timing=timing,
            effect_receipt=effect_receipt,
        )
        for key in (
            "interaction_kind",
            "authorization_required",
            "question_contract",
            "question_count",
            "missing_evidence_roles",
            "missing_fields",
            "interaction_route",
            "question",
            "failure_code",
            "retryable",
            "executed_external_effects",
            "recovery_route",
            "retry_after_seconds",
            "qualified_task_count",
        ):
            if key in boundary:
                status[key] = boundary[key]
        if "capacity_receipt_ref" in boundary:
            reference = boundary["capacity_receipt_ref"]
            if (
                not isinstance(reference, Mapping)
                or set(reference) not in (
                    {"path", "sha256"},
                    {"path", "sha256", "role"},
                )
                or not isinstance(reference.get("path"), str)
                or reference["path"].startswith("/")
                or ".." in reference["path"].split("/")
                or not isinstance(reference.get("sha256"), str)
                or SHA256_RE.fullmatch(reference["sha256"]) is None
                or (
                    "role" in reference
                    and not isinstance(reference.get("role"), str)
                )
            ):
                raise ValueError("capacity receipt reference is invalid")
            status["capacity_receipt_ref"] = deepcopy(dict(reference))
        return status

    def _project_record_ref(self, record: ProjectRecord) -> dict[str, str]:
        payload = record.projection_path.read_bytes()
        return {
            "path": record.projection_path.relative_to(self.root).as_posix(),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "role": "project_record_projection",
        }

    def _effect_receipt(self, record: ProjectRecord) -> dict:
        """Summarize authorized completed effects at the accepted-content horizon."""

        trace_directory = record.workspace / "run-traces"
        refs = []
        external_write_count = 0
        view = record.read_current_project_view()
        source_run_ids = sorted(
            {
                str(source["operation_run_id"])
                for fact in (view.get("facts") or {}).values()
                if isinstance(fact, Mapping)
                and fact.get("domain_kind") == "capability_result"
                and isinstance(fact.get("source"), Mapping)
                and isinstance(
                    (source := fact["source"]).get("operation_run_id"),
                    str,
                )
                and source["operation_run_id"]
            }
        )
        result = view.get("result")
        result_run_id = (
            result.get("interaction_run_id")
            if isinstance(result, Mapping)
            else None
        )
        if isinstance(result_run_id, str) and result_run_id:
            source_run_ids = sorted({*source_run_ids, result_run_id})
        trace_run_ids = [
            self.run_id,
            *(
                run_id
                for run_id in source_run_ids
                if run_id != self.run_id
            ),
        ]
        for expected_run_id in trace_run_ids:
            if (
                Path(expected_run_id).name != expected_run_id
                or "/" in expected_run_id
                or "\\" in expected_run_id
            ):
                raise ValueError("registered Run Trace identity is unsafe")
            path = trace_directory / f"{expected_run_id}.jsonl"
            first_line = next(
                (
                    line
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ),
                None,
            )
            if first_line is None:
                continue
            try:
                first_event = json.loads(first_line)
            except json.JSONDecodeError as error:
                raise ValueError("registered Run Trace is invalid") from error
            run_id = (
                first_event.get("run_id")
                if isinstance(first_event, Mapping)
                else None
            )
            if not isinstance(run_id, str) or not run_id:
                raise ValueError("registered Run Trace identity is absent")
            if run_id != expected_run_id:
                raise ValueError("registered Run Trace identity drifted")
            trace = RunTrace(path, run_id=run_id)
            events = trace.events()
            if not events:
                continue
            formal_indexes = [
                index
                for index, event in enumerate(events)
                if event.get("event_type")
                == "research.run.answer.formal_accepted"
            ]
            bounded_events = (
                events[: formal_indexes[-1] + 1]
                if formal_indexes
                else events
            )
            external_write_count += sum(
                event.get("event_type")
                == "research.run.capability.completed"
                and isinstance(event.get("payload"), Mapping)
                and event["payload"].get("effect") == "external_write"
                for event in bounded_events
            )
            trace_bytes = b"".join(
                canonical_json_bytes(event) + b"\n"
                for event in bounded_events
            )
            refs.append(
                {
                    "contract": "lca_research_kernel.run_trace_ref/v1",
                    "run_id": run_id,
                    "path": path.relative_to(self.root).as_posix(),
                    "last_event_sha256": bounded_events[-1][
                        "event_sha256"
                    ],
                    "trace_sha256": hashlib.sha256(trace_bytes).hexdigest(),
                    "event_count": len(bounded_events),
                }
            )
        if not refs:
            raise ValueError("Product Interface has no registered Run Trace")
        receipt = {
            "contract": "lca_research_kernel.run_effect_receipt/v1",
            "research_id": str(view["project_id"]),
            "effect_counts": {"external_write": external_write_count},
            "registered_run_trace_refs": refs,
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        return receipt

    def _public_execution_receipt(
        self,
        record: ProjectRecord,
        *,
        answer_timing: AnswerTiming,
        effect_receipt: Mapping[str, object],
    ) -> dict:
        """Project one content-bound operational receipt without model content."""

        if effect_receipt.get("receipt_sha256") != canonical_sha256(
            {
                key: deepcopy(value)
                for key, value in effect_receipt.items()
                if key != "receipt_sha256"
            }
        ):
            raise ValueError("run effect receipt integrity check failed")
        trace_refs = effect_receipt.get("registered_run_trace_refs")
        if not isinstance(trace_refs, list) or not trace_refs:
            raise ValueError("registered Run Trace references are unavailable")

        model_calls = []
        model_request_ids: set[str] = set()
        tool_count = 0
        public_trace_refs = []
        activity_started_ms: dict[str, int] = {}
        recorded_model_activity_ids: set[str] = set()
        answer_requested_at: list[datetime] = []
        model_requested_at: list[datetime] = []
        heartbeat_count = 0
        cpu_samples: list[int] = []
        elapsed_samples: list[int] = []
        checkpoint_event_counts: list[int] = []
        for reference in trace_refs:
            if not isinstance(reference, Mapping):
                raise ValueError("registered Run Trace reference is invalid")
            run_id = reference.get("run_id")
            relative_path = reference.get("path")
            event_count = reference.get("event_count")
            if (
                not isinstance(run_id, str)
                or not run_id
                or not isinstance(relative_path, str)
                or relative_path.startswith("/")
                or ".." in relative_path.split("/")
                or isinstance(event_count, bool)
                or not isinstance(event_count, int)
                or event_count < 1
            ):
                raise ValueError("registered Run Trace reference is invalid")
            path = (self.root / relative_path).resolve()
            if not path.is_relative_to(record.workspace.resolve()):
                raise ValueError("registered Run Trace escaped its project")
            events = RunTrace(path, run_id=run_id).events()
            if len(events) < event_count:
                raise ValueError("registered Run Trace is shorter than its receipt")
            bounded_events = events[:event_count]
            trace_bytes = b"".join(
                canonical_json_bytes(event) + b"\n" for event in bounded_events
            )
            if (
                bounded_events[-1].get("event_sha256")
                != reference.get("last_event_sha256")
                or hashlib.sha256(trace_bytes).hexdigest()
                != reference.get("trace_sha256")
            ):
                raise ValueError("registered Run Trace receipt drifted")
            public_trace_refs.append(
                {
                    "run_id": run_id,
                    "last_event_sha256": reference["last_event_sha256"],
                    "trace_sha256": reference["trace_sha256"],
                    "event_count": event_count,
                }
            )
            for event in bounded_events:
                event_type = event.get("event_type")
                payload = event.get("payload")
                if event_type in {
                    "research.run.answer.requested",
                    "research.run.model.requested",
                }:
                    occurred_at = event.get("occurred_at")
                    if isinstance(occurred_at, str):
                        try:
                            timestamp = datetime.fromisoformat(
                                occurred_at.replace("Z", "+00:00")
                            )
                        except ValueError:
                            timestamp = None
                        if timestamp is not None:
                            target = (
                                answer_requested_at
                                if event_type == "research.run.answer.requested"
                                else model_requested_at
                            )
                            target.append(timestamp)
                if not isinstance(payload, Mapping):
                    continue
                control_sample = None
                if event_type == "research.run.heartbeat":
                    heartbeat_count += 1
                    checkpoint = payload.get("checkpoint")
                    if not isinstance(checkpoint, Mapping):
                        raise ValueError("run heartbeat checkpoint is invalid")
                    control_sample = checkpoint
                    checkpoint_event_count = checkpoint.get("event_count")
                    if (
                        isinstance(checkpoint_event_count, bool)
                        or not isinstance(checkpoint_event_count, int)
                        or checkpoint_event_count < 0
                    ):
                        raise ValueError("run heartbeat event count is invalid")
                    checkpoint_event_counts.append(checkpoint_event_count)
                elif event_type == "research.run.step.started":
                    control_sample = payload
                if control_sample is not None:
                    cpu_ms = control_sample.get("cpu_ms")
                    elapsed_ms = control_sample.get("elapsed_ms")
                    if cpu_ms is not None:
                        if (
                            isinstance(cpu_ms, bool)
                            or not isinstance(cpu_ms, int)
                            or cpu_ms < 0
                        ):
                            raise ValueError("run control CPU evidence is invalid")
                        cpu_samples.append(cpu_ms)
                    if elapsed_ms is not None:
                        if (
                            isinstance(elapsed_ms, bool)
                            or not isinstance(elapsed_ms, int)
                            or elapsed_ms < 0
                        ):
                            raise ValueError("run control elapsed evidence is invalid")
                        elapsed_samples.append(elapsed_ms)
                if event_type == "research.run.answer.activity_started":
                    activity_id = payload.get("activity_id")
                    elapsed_ms = payload.get("elapsed_ms")
                    if (
                        not isinstance(activity_id, str)
                        or not activity_id
                        or isinstance(elapsed_ms, bool)
                        or not isinstance(elapsed_ms, int)
                        or elapsed_ms < 0
                    ):
                        raise ValueError("model request timing evidence is invalid")
                    activity_started_ms[activity_id] = elapsed_ms
                elif (
                    event_type == "research.run.answer.activity_recorded"
                    and payload.get("kind") == "model"
                ):
                    activity_id = payload.get("activity_id")
                    if not isinstance(activity_id, str) or not activity_id:
                        raise ValueError("model activity identity is invalid")
                    recorded_model_activity_ids.add(activity_id)
                if event_type == "research.run.model.completed":
                    usage = payload.get("usage")
                    if not isinstance(usage, Mapping):
                        raise ValueError("model completion usage is invalid")
                    request_id = payload.get("request_id")
                    if not isinstance(request_id, str) or not request_id:
                        raise ValueError("model completion request identity is invalid")
                    model_request_ids.add(request_id)
                    model_calls.append(
                        {
                            "provider": payload.get("provider"),
                            "model": payload.get("model"),
                            "adapter_id": payload.get("adapter_id"),
                            "duration_ms": payload.get("duration_ms"),
                            "usage": deepcopy(dict(usage)),
                        }
                    )
                elif event_type in {
                    "research.run.tool.completed",
                    "research.run.capability.completed",
                }:
                    tool_count += 1

        timing = answer_timing.receipt()
        timing_counts = timing.get("counts")
        if not isinstance(timing_counts, Mapping):
            raise ValueError("answer timing counts are unavailable")
        effect_counts = effect_receipt.get("effect_counts")
        if not isinstance(effect_counts, Mapping):
            raise ValueError("run effect counts are unavailable")
        first_model_request_ms = None
        if model_calls:
            relevant_activity_ids = model_request_ids | recorded_model_activity_ids
            starts = [
                activity_started_ms[activity_id]
                for activity_id in relevant_activity_ids
                if activity_id in activity_started_ms
            ]
            if starts:
                first_model_request_ms = min(starts)
            elif answer_requested_at and model_requested_at:
                wall_delta_ms = int(
                    (
                        min(model_requested_at) - min(answer_requested_at)
                    ).total_seconds()
                    * 1000
                )
                if 0 <= wall_delta_ms <= timing.get("hard_boundary_ms", 0):
                    first_model_request_ms = wall_delta_ms
        current_view = record.read_current_project_view()
        audit = record.read_audit_trail()
        accepted_result_count = 0
        accepted_gap_resolution_count = 0
        for event in audit:
            if event.get("event_type") != "research.project.accepted_change":
                continue
            payload = event.get("payload")
            candidate = (
                payload.get("candidate") if isinstance(payload, Mapping) else None
            )
            if not isinstance(candidate, Mapping):
                continue
            if candidate.get("change_type") == "result.accepted":
                accepted_result_count += 1
            fact = candidate.get("fact")
            if (
                candidate.get("change_type") == "fact.upserted"
                and isinstance(fact, Mapping)
                and fact.get("domain_kind") == "material_gap_resolution"
            ):
                accepted_gap_resolution_count += 1
        receipt = {
            "contract": "lca_research_kernel.public_execution_receipt/v1",
            "research_id": str(current_view["project_id"]),
            "counts": {
                "model": len(model_calls),
                "tool": tool_count,
                "external_read": timing_counts.get("external_read"),
                "external_write": effect_counts.get("external_write"),
                "useless_retry": timing_counts.get("useless_retry"),
            },
            "model_calls": model_calls,
            "first_model_request_ms": first_model_request_ms,
            "elapsed_ms": deepcopy(timing.get("elapsed_ms")),
            "durations_ms": deepcopy(timing.get("durations_ms")),
            "trace_refs": public_trace_refs,
            "project_record": {
                "last_event_sha256": current_view["last_event_sha256"],
                "event_count": len(audit),
                "accepted_change_counts": {
                    "formal_result": accepted_result_count,
                    "gap_resolution": accepted_gap_resolution_count,
                },
            },
            "run_control": {
                "heartbeat_count": heartbeat_count,
                "cpu_sample_count": len(cpu_samples),
                "max_observed_cpu_ms": max(cpu_samples) if cpu_samples else None,
                "max_observed_elapsed_ms": (
                    max(elapsed_samples) if elapsed_samples else None
                ),
                "max_checkpoint_event_count": (
                    max(checkpoint_event_counts)
                    if checkpoint_event_counts
                    else None
                ),
            },
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        return receipt
