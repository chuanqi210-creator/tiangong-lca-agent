"""Composition-time binding for opaque material revision resolution."""

from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from typing import Callable, Mapping


class MaterialResolutionBinding:
    """Share resolver dependencies without widening the ProductResearchPort."""

    def __init__(
        self,
        *,
        verified_content_resolver: Callable[
            [str, str, Mapping[str, object]], bytes
        ]
        | None = None,
        material_revision_resolver: Callable[
            [str, list[Mapping[str, object]]], list[Mapping[str, object]]
        ]
        | None = None,
    ):
        self._lock = threading.RLock()
        self._verified_content_resolver = verified_content_resolver
        self._material_revision_resolver = material_revision_resolver
        self._material_selections: dict[
            str,
            tuple[
                str,
                list[dict[str, object]],
                dict[str, object],
            ],
        ] = {}

    def bind(
        self,
        *,
        verified_content_resolver: Callable[
            [str, str, Mapping[str, object]], bytes
        ],
        material_revision_resolver: Callable[
            [str, list[Mapping[str, object]]], list[Mapping[str, object]]
        ],
    ) -> None:
        if not callable(verified_content_resolver) or not callable(
            material_revision_resolver
        ):
            raise ValueError("material resolution binding is invalid")
        with self._lock:
            self._verified_content_resolver = verified_content_resolver
            self._material_revision_resolver = material_revision_resolver

    def resolve_verified_content(
        self,
        project_id: str,
        content_kind: str,
        reference: Mapping[str, object],
    ) -> bytes:
        with self._lock:
            resolver = self._verified_content_resolver
        if not callable(resolver):
            raise ValueError("verified material content resolution is unavailable")
        return resolver(project_id, content_kind, deepcopy(dict(reference)))

    def resolve_revisions(
        self,
        project_id: str,
        revision_refs: list[Mapping[str, object]],
    ) -> list[Mapping[str, object]]:
        with self._lock:
            resolver = self._material_revision_resolver
        if not callable(resolver):
            raise ValueError("material revision resolution is unavailable")
        return resolver(
            project_id,
            [deepcopy(dict(reference)) for reference in revision_refs],
        )

    def register_material_selection(
        self,
        project_id: str,
        revision_refs: list[Mapping[str, object]],
        *,
        binding_context: Mapping[str, object] | None = None,
    ) -> dict[str, str]:
        """Return a content-bound opaque handle for one immutable selection.

        Revision identities remain inside the composition-time binding.  The
        Product Interface and its port consumers exchange only the digest.
        A worker may register the same selection again after restart; the
        handle is deterministic and resolution remains idempotent.
        """

        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("material selection project identity is invalid")
        if not isinstance(revision_refs, list) or any(
            not isinstance(item, Mapping) for item in revision_refs
        ):
            raise ValueError("material selection revisions are invalid")
        if not revision_refs and binding_context is None:
            raise ValueError("material selection revisions are invalid")
        normalized = [deepcopy(dict(reference)) for reference in revision_refs]
        normalized_context = (
            deepcopy(dict(binding_context))
            if binding_context is not None
            else {}
        )
        identity = {
            "contract": "lca_research_kernel.material_selection_binding/v1",
            "project_id": project_id,
            "revision_refs": normalized,
            "binding_context": normalized_context,
        }
        digest = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self._lock:
            previous = self._material_selections.get(digest)
            candidate = (project_id, normalized, normalized_context)
            if previous is not None and previous != candidate:
                raise ValueError("material selection identity collision")
            self._material_selections[digest] = candidate
        return {"sha256": digest}

    def resolve_material_selection(
        self,
        project_id: str,
        selection_ref: Mapping[str, object],
    ) -> list[Mapping[str, object]]:
        """Resolve and consume an opaque handle through the bound resolver."""

        resolved = self.resolve_material_selection_binding(
            project_id, selection_ref
        )
        return resolved["material_bundles"]

    def resolve_material_selection_binding(
        self,
        project_id: str,
        selection_ref: Mapping[str, object],
    ) -> dict[str, object]:
        """Open one binding inside the product boundary, including local context."""

        if not isinstance(selection_ref, Mapping) or set(selection_ref) != {
            "sha256"
        }:
            raise ValueError("material selection binding reference is invalid")
        digest = selection_ref.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("material selection binding reference is invalid")
        with self._lock:
            registered = self._material_selections.get(digest)
            if registered is None or registered[0] != project_id:
                raise ValueError("material selection binding is unavailable")
            del self._material_selections[digest]
        bundles = (
            self.resolve_revisions(project_id, registered[1])
            if registered[1]
            else []
        )
        return {
            "material_bundles": bundles,
            "binding_context": deepcopy(registered[2]),
        }
