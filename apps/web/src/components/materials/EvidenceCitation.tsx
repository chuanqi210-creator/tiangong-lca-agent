"use client";

import { FileSearchIcon } from "lucide-react";
import { copy } from "@/lib/copy";
import { citationLocatorLabel } from "@/lib/d18/citations";
import { sourceAuthorityLabel } from "@/lib/d18/source-use";
import type { EvidenceCitation as EvidenceCitationValue } from "@/lib/d18/types";

export function EvidenceCitation({
  citation,
  ordinal,
  active = false,
  onOpen,
}: {
  citation: EvidenceCitationValue;
  ordinal: number;
  active?: boolean;
  onOpen: (citation: EvidenceCitationValue) => void;
}) {
  const authority = sourceAuthorityLabel(citation.sourceAuthority);
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={`${copy.result.openCitation}：${citationLocatorLabel(citation)}`}
      onClick={() => onOpen(citation)}
      className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-background/70 px-2 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:border-chart-1/60 hover:text-foreground aria-pressed:border-chart-1 aria-pressed:bg-chart-1/10 aria-pressed:text-foreground"
    >
      <FileSearchIcon aria-hidden="true" className="size-3.5 shrink-0 text-chart-1" />
      <span className="shrink-0 font-mono text-[10px] text-chart-1">[{ordinal}]</span>
      <span className="min-w-0 truncate">{citationLocatorLabel(citation)}</span>
      <span
        className={
          citation.sourceAuthority.startsWith("tiangong")
            ? "shrink-0 rounded bg-chart-1/15 px-1 py-0.5 font-mono text-[9px] text-chart-1"
            : "shrink-0 rounded bg-muted px-1 py-0.5 font-mono text-[9px]"
        }
      >
        {authority}
      </span>
    </button>
  );
}
