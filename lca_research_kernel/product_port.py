"""Deep external port for the D-18 Product Interface."""

from __future__ import annotations

from typing import Mapping, Protocol


PRODUCT_RESEARCH_ACTIONS = frozenset(
    {"submit_research", "submit_input", "read_status", "read_result"}
)

# Preserved only for historical evaluators and component-level recovery tests.
# Production adapters must depend on ProductResearchPort, never on these names.
LEGACY_PRODUCT_INTERFACE_ACTIONS = frozenset(
    {
        "start_research",
        "start_research_with_materials",
        "supply_material_revisions",
        "supply_material_or_decision",
        "record_material_recovery_decision",
        "continue_research",
        "resume_research",
        "current_material_revision_refs",
    }
)


class ProductResearchPort(Protocol):
    """The only research actions visible above the Product Interface boundary."""

    def submit_research(
        self,
        request: Mapping[str, object],
    ) -> dict: ...

    def submit_input(
        self,
        research_id: str,
        external_input: Mapping[str, object],
    ) -> dict: ...

    def read_status(self, research_id: str) -> dict: ...

    def read_result(self, research_id: str) -> dict: ...
