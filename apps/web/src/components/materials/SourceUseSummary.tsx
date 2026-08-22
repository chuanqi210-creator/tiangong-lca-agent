import { copy } from "@/lib/copy";
import { groupSourceUse, sourceAuthorityLabel } from "@/lib/d18/source-use";
import type { SourceUse } from "@/lib/d18/types";

const groupKeys = ["used", "considered", "partial", "conflicts"] as const;

function reasonLabel(reason: string): string {
  const reasons = copy.result.sourceUseReasons as Record<string, string>;
  return reasons[reason] ?? reason;
}

export function SourceUseSummary({ sources }: { sources: SourceUse[] }) {
  if (sources.length === 0) return null;
  const grouped = groupSourceUse(sources);
  return (
    <section aria-label={copy.result.sourceUse} className="mt-3 space-y-2">
      <p className="font-mono text-[10px] tracking-widest text-muted-foreground">
        {copy.result.sourceUse}
      </p>
      {groupKeys.map((key) => {
        const rows = grouped[key];
        if (rows.length === 0) return null;
        return (
          <div key={key} className="rounded-md border border-border/80 bg-background/50 p-2">
            <p className="text-xs font-medium text-foreground">
              {copy.result.sourceUseGroups[key]}
            </p>
            <ul className="mt-1.5 space-y-1.5">
              {rows.map((source) => (
                <li key={`${source.materialId}:${source.revisionId}`} className="text-xs text-muted-foreground">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-medium text-foreground">{source.displayName}</span>
                    <span className="rounded bg-muted px-1 py-0.5 font-mono text-[9px]">
                      {sourceAuthorityLabel(source.sourceAuthority)}
                    </span>
                    <span className="font-mono text-[9px]">{source.processingStatus}</span>
                  </div>
                  <p className="mt-0.5">{reasonLabel(source.reason)}</p>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </section>
  );
}
