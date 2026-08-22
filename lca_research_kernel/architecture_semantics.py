"""Fail-closed D-18 architecture semantics and auditable route frames."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Sequence

from .agent_bootstrap import semantic_fingerprint


SEMANTICS_CONTRACT = "lca_research_kernel.d18_runtime_architecture_semantics/v1"
DECISION_FRAME_CONTRACT = "lca_research_kernel.architecture_decision_frame/v1"
EXPECTED_MODULE_IDS = {
    "product_interface",
    "research_orchestrator",
    "lca_domain_kernel",
    "model_gateway",
    "capability_gateway",
    "project_record",
    "assurance_boundary",
}
MODULE_FIELDS = {
    "id",
    "kind",
    "architecture_projection_sha256",
    "purpose",
    "triggers",
    "inputs",
    "artifacts",
    "forbidden",
    "dependencies",
    "failure_boundaries",
}
PROJECTION_FIELDS = ("id", "kind", "owns", "accepts", "produces", "must_not")
HIDDEN_REASONING_FIELDS = {
    "chain_of_thought",
    "hidden_reasoning",
    "internal_monologue",
    "scratchpad",
}


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


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unavailable or invalid") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_nonempty_strings(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(f"{label} must be a non-empty string list")
    return list(value)


def _has_hidden_reasoning(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in HIDDEN_REASONING_FIELDS or _has_hidden_reasoning(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_hidden_reasoning(item) for item in value)
    return False


class ArchitectureSemantics:
    """Compile D-18 module ownership into deterministic state-change routing."""

    def __init__(self, contract: Mapping[str, object]):
        self.contract = deepcopy(dict(contract))
        self.modules = deepcopy(list(self.contract["modules"]))  # type: ignore[arg-type]
        self.module_ids = tuple(row["id"] for row in self.modules)
        self._by_id = {row["id"]: row for row in self.modules}

    @classmethod
    def load(cls, root: Path, path: Path) -> "ArchitectureSemantics":
        runtime_root = Path(root).resolve()
        contract = _read_object(Path(path), "architecture semantics contract")
        expected_fields = {
            "contract",
            "contract_id",
            "architecture_ref",
            "ownership",
            "selection_policy",
            "modules",
            "frame_contract",
        }
        if set(contract) != expected_fields or contract.get("contract") != SEMANTICS_CONTRACT:
            raise ValueError("architecture semantics contract fields are unsupported")
        reference = contract.get("architecture_ref")
        if not isinstance(reference, Mapping):
            raise ValueError("architecture source reference is absent")
        raw_path = reference.get("path")
        if not isinstance(raw_path, str):
            raise ValueError("architecture source path is absent")
        relative = Path(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("architecture source path is unsafe")
        architecture_path = runtime_root / relative
        try:
            architecture_path.resolve().relative_to(runtime_root)
        except ValueError as error:
            raise ValueError("architecture source path escapes runtime root") from error
        if (
            not architecture_path.is_file()
            or _file_sha256(architecture_path) != reference.get("sha256")
        ):
            raise ValueError("architecture source drifted")
        architecture = _read_object(architecture_path, "D-18 architecture")
        if semantic_fingerprint(architecture) != reference.get("semantic_fingerprint"):
            raise ValueError("architecture semantic fingerprint drifted")
        cls._validate_contract(contract, architecture)
        return cls(contract)

    @staticmethod
    def _validate_contract(
        contract: Mapping[str, object], architecture: Mapping[str, object]
    ) -> None:
        ownership = contract.get("ownership")
        if not isinstance(ownership, Mapping) or ownership != {
            "architecture_semantics": "research_orchestrator",
            "accepted_project_truth": "project_record",
            "accepted_write_gate": "assurance_boundary",
            "official_lca_authority": "TianGong",
        }:
            raise ValueError("architecture semantics ownership drifted")
        selection = contract.get("selection_policy")
        if (
            not isinstance(selection, Mapping)
            or selection.get("basis") != "state_transition_and_responsibility"
            or any(
                selection.get(key) is not True
                for key in (
                    "requires_expected_state_change",
                    "requires_bounded_alternatives",
                    "provider_name_is_not_priority",
                    "keyword_match_is_not_selection",
                    "case_identity_is_not_selection",
                    "hidden_reasoning_is_forbidden",
                )
            )
        ):
            raise ValueError("architecture selection policy drifted")
        architecture_modules = architecture.get("modules")
        modules = contract.get("modules")
        if not isinstance(architecture_modules, list) or not isinstance(modules, list):
            raise ValueError("architecture module descriptors are absent")
        source_by_id = {
            row["id"]: row
            for row in architecture_modules
            if isinstance(row, Mapping) and isinstance(row.get("id"), str)
        }
        ids = [
            row.get("id") if isinstance(row, Mapping) else None for row in modules
        ]
        if len(ids) != 7 or set(ids) != EXPECTED_MODULE_IDS or len(set(ids)) != len(ids):
            raise ValueError("architecture semantics must compile exactly seven modules")
        for row in modules:
            if not isinstance(row, Mapping) or set(row) != MODULE_FIELDS:
                raise ValueError("architecture module semantic fields drifted")
            module_id = row["id"]
            source = source_by_id.get(module_id)
            if source is None or row.get("kind") != source.get("kind"):
                raise ValueError(f"{module_id} architecture module identity drifted")
            projection = {key: source[key] for key in PROJECTION_FIELDS}
            if _sha256(projection) != row.get("architecture_projection_sha256"):
                raise ValueError(f"{module_id} architecture projection drifted")
            for key in (
                "purpose",
                "triggers",
                "inputs",
                "artifacts",
                "forbidden",
                "dependencies",
                "failure_boundaries",
            ):
                _require_nonempty_strings(row.get(key), f"{module_id} {key}")
        frame = contract.get("frame_contract")
        if (
            not isinstance(frame, Mapping)
            or frame.get("contract") != DECISION_FRAME_CONTRACT
            or set(frame.get("forbidden_fields") or []) != HIDDEN_REASONING_FIELDS
        ):
            raise ValueError("architecture decision frame contract drifted")

    def frame_sha256(self, value: Mapping[str, object]) -> str:
        normalized = deepcopy(dict(value))
        normalized.pop("frame_sha256", None)
        return _sha256(normalized)

    def validate_frame(self, frame: Mapping[str, object]) -> dict:
        if not isinstance(frame, Mapping) or frame.get("contract") != DECISION_FRAME_CONTRACT:
            raise ValueError("architecture decision frame contract is unsupported")
        required = self.contract["frame_contract"]["required_fields"]  # type: ignore[index]
        if set(frame) != set(required):
            raise ValueError("architecture decision frame fields are incomplete or unsupported")
        if _has_hidden_reasoning(frame):
            raise ValueError("architecture decision frame contains hidden reasoning")
        if frame.get("frame_sha256") != self.frame_sha256(frame):
            raise ValueError("architecture decision frame hash drifted")
        if frame.get("selection_basis") != "state_transition_and_responsibility":
            raise ValueError("architecture decision frame selection basis is invalid")
        modules = _require_nonempty_strings(
            frame.get("selected_modules"), "selected modules"
        )
        if len(modules) != len(set(modules)):
            raise ValueError("architecture decision frame has duplicate modules")
        if not set(modules).issubset(EXPECTED_MODULE_IDS):
            raise ValueError("architecture decision frame selects an unknown module")
        capabilities = frame.get("selected_capabilities")
        if not isinstance(capabilities, list) or any(
            not isinstance(item, str) or not item.strip() for item in capabilities
        ):
            raise ValueError("selected capabilities are invalid")
        if len(capabilities) != len(set(capabilities)):
            raise ValueError("architecture decision frame has duplicate capabilities")
        problem = frame.get("current_problem")
        if not isinstance(problem, Mapping):
            raise ValueError("architecture decision problem is absent")
        required_responsibility = problem.get("required_responsibility")
        if required_responsibility not in modules:
            raise ValueError("architecture decision responsibility is assigned incorrectly")
        evidence_refs = problem.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise ValueError("architecture decision evidence basis is absent")
        expected = frame.get("expected_state_change")
        if not isinstance(expected, Mapping):
            raise ValueError("architecture expected state change is absent")
        from_state = expected.get("from_state")
        to_state = expected.get("to_state")
        if (
            not isinstance(from_state, str)
            or not isinstance(to_state, str)
            or not from_state
            or from_state == to_state
        ):
            raise ValueError("architecture expected state change is not observable")
        _require_nonempty_strings(
            expected.get("observable_delta"), "expected state observable delta"
        )
        _require_nonempty_strings(
            frame.get("architecture_principles"), "architecture principles"
        )
        _require_nonempty_strings(
            frame.get("acceptance_conditions"), "acceptance conditions"
        )
        reason = frame.get("auditable_reason")
        maximum = self.contract["frame_contract"]["maximum_reason_characters"]  # type: ignore[index]
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > maximum
        ):
            raise ValueError("architecture auditable reason is invalid")
        alternatives = frame.get("alternatives")
        if not isinstance(alternatives, list):
            raise ValueError("architecture alternatives are absent")
        for alternative in alternatives:
            if (
                not isinstance(alternative, Mapping)
                or not isinstance(alternative.get("route_id"), str)
                or not isinstance(alternative.get("reason_not_selected"), str)
                or not alternative["reason_not_selected"].strip()
            ):
                raise ValueError("architecture alternative is incomplete")
        if not isinstance(frame.get("next_route"), str) or not frame["next_route"]:
            raise ValueError("architecture decision has no unique next route")
        return deepcopy(dict(frame))

    def select_by_state_change(
        self,
        *,
        goal_ref: Mapping[str, object],
        project_state_ref: Mapping[str, object],
        current_problem: Mapping[str, object],
        expected_state_change: Mapping[str, object],
        candidate_routes: Sequence[Mapping[str, object]],
    ) -> dict:
        responsibility = current_problem.get("required_responsibility")
        from_state = expected_state_change.get("from_state")
        to_state = expected_state_change.get("to_state")
        if (
            responsibility not in EXPECTED_MODULE_IDS
            or not isinstance(from_state, str)
            or not isinstance(to_state, str)
            or not from_state
            or from_state == to_state
        ):
            raise ValueError("expected state change and responsibility are required")
        matches = [
            deepcopy(dict(route))
            for route in candidate_routes
            if isinstance(route, Mapping)
            and route.get("responsibility") == responsibility
            and route.get("from_state") == from_state
            and route.get("to_state") == to_state
        ]
        if len(matches) != 1:
            raise ValueError("architecture route selection is ambiguous or unavailable")
        selected = matches[0]
        alternatives = [
            {
                "route_id": str(route.get("route_id")),
                "expected_state_change": {
                    "from_state": route.get("from_state"),
                    "to_state": route.get("to_state"),
                },
                "reason_not_selected": (
                    "The route does not match both the required responsibility "
                    "and the expected observable state transition."
                ),
            }
            for route in candidate_routes
            if route is not selected and route.get("route_id") != selected.get("route_id")
        ]
        basis = {
            "goal_ref": deepcopy(dict(goal_ref)),
            "project_state_ref": deepcopy(dict(project_state_ref)),
            "current_problem": deepcopy(dict(current_problem)),
            "selected_route": selected["route_id"],
            "expected_state_change": deepcopy(dict(expected_state_change)),
        }
        frame = {
            "contract": DECISION_FRAME_CONTRACT,
            "decision_id": "architecture-decision-" + _sha256(basis)[:24],
            "goal_ref": deepcopy(dict(goal_ref)),
            "project_state_ref": deepcopy(dict(project_state_ref)),
            "current_problem": deepcopy(dict(current_problem)),
            "selected_modules": [str(selected["responsibility"])],
            "selected_capabilities": deepcopy(selected.get("capability_ids", [])),
            "architecture_principles": deepcopy(
                selected.get("architecture_principles", [])
            ),
            "expected_state_change": deepcopy(dict(expected_state_change)),
            "acceptance_conditions": deepcopy(
                selected.get("acceptance_conditions", [])
            ),
            "permission_boundary": deepcopy(
                selected.get("permission_boundary", {})
            ),
            "claim_ceiling": selected.get("claim_ceiling"),
            "alternatives": alternatives,
            "auditable_reason": selected.get("auditable_reason"),
            "selection_basis": "state_transition_and_responsibility",
            "next_route": selected.get("route_id"),
        }
        frame["frame_sha256"] = self.frame_sha256(frame)
        return self.validate_frame(frame)
