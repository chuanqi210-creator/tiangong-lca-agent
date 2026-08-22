"""Compose independent Assurance with Project Record commit authority."""

from __future__ import annotations

from copy import deepcopy
from typing import Mapping, Sequence

from .accepted_state import apply_accepted_candidate
from .change_contracts import validate_project_candidate


class AssuredProjectRecord:
    """Evaluate outside Project Record, then commit one content-bound batch.

    Project Record validates identity, content and acceptance bindings but never
    runs the scientific evaluator.  This coordinator owns the evaluate/commit
    handshake and binds it to the exact head observed before evaluation.
    """

    def __init__(self, record: object, assurance: object):
        evaluator = getattr(assurance, "evaluate_candidate_change", None)
        if not callable(evaluator):
            raise ValueError("Assurance candidate evaluator is unavailable")
        self.record = record
        self.evaluate_candidate_change = evaluator

    def accept_material_changes(
        self, entries: Sequence[Mapping[str, object]]
    ) -> list[dict]:
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
            raise ValueError("assured material change batch is invalid")
        view = self.record.read_current_project_view()
        expected_head = view.get("last_event_sha256")
        existing_by_key = {
            event["idempotency_key"]: event
            for event in self.record.read_accepted_material_change_receipts()
        }
        assured_entries = []
        for entry in entries:
            if not isinstance(entry, Mapping) or set(entry) != {
                "candidate",
                "idempotency_key",
                "verified_content",
            }:
                raise ValueError("assured material change batch entry is invalid")
            candidate = validate_project_candidate(
                entry["candidate"],
                require_material_bindings=True,
                require_evidence_units_ref=True,
            )
            key = entry["idempotency_key"]
            existing = existing_by_key.get(key)
            if existing is not None:
                payload = existing.get("payload")
                if (
                    not isinstance(payload, Mapping)
                    or payload.get("candidate") != candidate
                    or not isinstance(payload.get("assurance_acceptance"), Mapping)
                ):
                    raise ValueError(
                        f"Idempotency key '{key}' was reused with different content"
                    )
                acceptance = payload["assurance_acceptance"]
            else:
                evaluation_view = view
                if candidate["change_type"] == "evidence.locator_accepted":
                    evaluation_view = self.record.material_locator_authority_view(
                        view,
                        str(candidate["evidence_locator"]["revision_id"]),
                    )
                acceptance = self.evaluate_candidate_change(
                    candidate,
                    evaluation_view,
                    verified_content=deepcopy(entry["verified_content"]),
                )
                if not isinstance(acceptance, Mapping) or acceptance.get(
                    "decision"
                ) != "accepted":
                    errors = (
                        acceptance.get("errors")
                        if isinstance(acceptance, Mapping)
                        else ["assurance_acceptance_absent"]
                    )
                    raise ValueError(
                        "material candidate failed assurance: "
                        + ",".join(str(error) for error in errors)
                    )
                apply_accepted_candidate(view, candidate)
            assured_entries.append(
                {
                    "candidate": candidate,
                    "idempotency_key": key,
                    "verified_content": deepcopy(entry["verified_content"]),
                    "acceptance": deepcopy(dict(acceptance)),
                }
            )
        return self.record.append_accepted_material_changes(
            assured_entries,
            expected_last_event_sha256=expected_head,
        )
