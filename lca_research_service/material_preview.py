"""Bounded, inert projections of candidate material evidence."""

from __future__ import annotations

import html
import io
from copy import deepcopy
from typing import Any, Mapping, Sequence

from PIL import Image


def reencode_image_preview(payload: bytes, *, max_edge: int = 1600) -> bytes:
    """Return a metadata-free PNG thumbnail, never the original image bytes."""

    if not isinstance(max_edge, int) or isinstance(max_edge, bool) or max_edge < 1:
        raise ValueError("image preview edge must be a positive integer")
    with Image.open(io.BytesIO(payload)) as image:
        image.load()
        image.thumbnail((max_edge, max_edge))
        clean = image.copy().convert(image.mode if image.mode in {"RGB", "RGBA", "L"} else "RGB")
        output = io.BytesIO()
        clean.save(output, format="PNG", optimize=True)
        return output.getvalue()


def build_preview(
    units: Sequence[Mapping[str, Any]],
    *,
    detected_mime: str,
    ceilings: Mapping[str, int],
) -> dict[str, Any]:
    """Build a JSON-safe preview without exposing immutable original bytes."""

    if detected_mime in {"text/csv", "text/tab-separated-values"} or any(
        unit.get("kind") == "table_row" for unit in units
    ):
        row_limit = max(0, int(ceilings.get("spreadsheet_rows", 200)))
        column_limit = max(0, int(ceilings.get("spreadsheet_columns", 50)))
        selected = [unit for unit in units if unit.get("kind") == "table_row"]
        rows = []
        for unit in selected[:row_limit]:
            cells = []
            for cell in list(unit.get("cells", []))[:column_limit]:
                cells.append(
                    {
                        "displayed_value": cell.get("displayed_value"),
                        "raw_value": cell.get("raw_value"),
                        "formula": cell.get("formula") is True,
                        "calculated_truth": False,
                    }
                )
            rows.append({"cells": cells, "source_span": deepcopy(unit.get("source_span"))})
        truncated = len(selected) > row_limit or any(
            len(list(unit.get("cells", []))) > column_limit for unit in selected[:row_limit]
        )
        return {"kind": "spreadsheet", "rows": rows, "truncated": truncated, "candidate_only": True}

    textual = [
        unit
        for unit in units
        if isinstance(unit.get("content"), str) and isinstance(unit.get("source_span"), Mapping)
    ]
    if textual:
        limit = max(0, int(ceilings.get("text_characters", 20_000)))
        remaining = limit
        excerpts = []
        for unit in textual:
            if remaining <= 0:
                break
            value = str(unit["content"])[:remaining]
            remaining -= len(value)
            excerpts.append(
                {
                    "escaped_text": html.escape(value, quote=True),
                    "source_span": deepcopy(unit["source_span"]),
                }
            )
        return {
            "kind": "text",
            "excerpts": excerpts,
            "truncated": sum(len(str(unit["content"])) for unit in textual) > limit,
            "candidate_only": True,
        }

    metadata = []
    for unit in units:
        metadata.append(
            {
                key: deepcopy(unit[key])
                for key in ("kind", "metadata", "source_span")
                if key in unit
            }
        )
    return {
        "kind": "metadata_only",
        "detected_mime": detected_mime,
        "metadata": metadata,
        "candidate_only": True,
    }
