"use client";

import { useEffect, useState } from "react";
import { copy } from "@/lib/copy";
import {
  getProjectMaterialPreview,
  type MaterialPreviewResponse,
} from "@/lib/d18/material-client";

function locatorLabel(locator: Record<string, unknown>) {
  return Object.entries(locator)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" · ");
}

function matchesHighlight(
  sourceSpan: Record<string, unknown> | undefined,
  highlight: MaterialPreviewResponse["highlight"],
) {
  if (!sourceSpan || !highlight) return false;
  return sourceSpan.kind === highlight.locator_kind
    && JSON.stringify(sourceSpan.locator) === JSON.stringify(highlight.locator);
}

export function MaterialPreview({
  researchId,
  materialId,
  initialLocatorId,
  fetcher = globalThis.fetch,
}: {
  researchId: string;
  materialId: string;
  initialLocatorId?: string;
  fetcher?: typeof fetch;
}) {
  const [projection, setProjection] = useState<MaterialPreviewResponse | null>(null);
  const [highlightId, setHighlightId] = useState<string | undefined>(initialLocatorId);

  useEffect(() => {
    let current = true;
    void getProjectMaterialPreview(researchId, materialId, highlightId, fetcher)
      .then((response) => {
        if (current) setProjection(response);
      })
      .catch(() => {
        if (current) setProjection(null);
      });
    return () => {
      current = false;
    };
  }, [fetcher, highlightId, materialId, researchId]);

  if (!projection) return <p className="text-xs text-muted-foreground">{copy.materialLibrary.previewUnavailable}</p>;
  return (
    <section aria-label={copy.materialLibrary.preview} className="space-y-2">
      {projection.preview.kind === "text" && (
        <div className="max-h-52 overflow-auto rounded border border-border bg-muted/30 p-2 font-mono text-xs leading-5">
          {projection.preview.excerpts?.map((excerpt, index) => (
            <p
              key={index}
              data-highlighted={matchesHighlight(excerpt.source_span, projection.highlight) || undefined}
              className={matchesHighlight(excerpt.source_span, projection.highlight) ? "rounded bg-chart-1/15 outline outline-1 outline-chart-1" : undefined}
            >
              {excerpt.escaped_text}
            </p>
          ))}
        </div>
      )}
      {projection.preview.kind === "spreadsheet" && (
        <div className="max-h-52 overflow-auto rounded border border-border">
          <table className="w-full text-xs">
            <tbody>
              {projection.preview.rows?.map((row, rowIndex) => (
                <tr
                  key={rowIndex}
                  data-highlighted={matchesHighlight(row.source_span, projection.highlight) || undefined}
                  className={matchesHighlight(row.source_span, projection.highlight) ? "bg-chart-1/15 outline outline-1 outline-chart-1" : undefined}
                >
                  {row.cells.map((cell, cellIndex) => (
                    <td key={cellIndex} className="border border-border px-2 py-1">
                      {String(cell.displayed_value ?? cell.raw_value ?? "")}
                      {cell.formula ? ` ${copy.materialLibrary.formulaMarker}` : ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(projection.preview.thumbnail_available || projection.preview.available_pages?.length) && (
        // Server returns only a generated, metadata-free PNG, never the original.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          alt={copy.materialLibrary.generatedPreviewAlt}
          src={`/api/d18/research/${encodeURIComponent(researchId)}/materials/${encodeURIComponent(materialId)}/preview-image${projection.preview.available_pages?.[0] ? `?page=${projection.preview.available_pages[0]}` : ""}`}
          className="max-h-52 max-w-full rounded border border-border object-contain"
        />
      )}
      {projection.locators.length > 0 && (
        <div>
          <p className="font-mono text-[10px] text-muted-foreground">{copy.materialLibrary.exactLocators}</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {projection.locators.map((locator) => (
              <button
                key={locator.locator_id}
                type="button"
                onClick={() => setHighlightId(locator.locator_id)}
                aria-pressed={projection.highlight?.locator_id === locator.locator_id}
                className="rounded border border-border px-1.5 py-1 font-mono text-[10px] text-muted-foreground aria-pressed:border-chart-1 aria-pressed:text-chart-1"
              >
                {locatorLabel(locator.locator)}
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
