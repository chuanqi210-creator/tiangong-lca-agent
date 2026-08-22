"use client";

import { useState } from "react";
import { FlaskConicalIcon, TriangleAlertIcon } from "lucide-react";
import { MessageResponse } from "@/components/ai-elements/message";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import { citationLocatorId } from "@/lib/d18/client";
import type { EvidenceCitation as EvidenceCitationValue } from "@/lib/d18/types";
import { EvidenceCitation } from "@/components/materials/EvidenceCitation";
import { MaterialPreview } from "@/components/materials/MaterialPreview";
import { SourceUseSummary } from "@/components/materials/SourceUseSummary";
import { AttachmentCard } from "./AttachmentCard";
import type { ResultCardData } from "./types";

/**
 * 结果：固定四层信息架构（结论 / 依据 / 限制 / 可带走的成果），
 * 卡片容器（rounded-xl border bg-card/50）+ 图标标题头 + hairline 分区，
 * 正文走 streamdown 渲染；正常结论用青绿标识，受限结论用暗橙。
 * 成果层附可下载附件。
 */
export function ResultCard({ card }: { card: ResultCardData }) {
  const [activeCitation, setActiveCitation] = useState<EvidenceCitationValue | null>(null);
  const citations = card.citations ?? [];
  const officialSources = card.officialSources ?? [];
  const sourceUse = card.sourceUse ?? [];
  const limited = card.variant === "limited";
  const lastLayer = card.layers.length - 1;
  const Icon = limited ? TriangleAlertIcon : FlaskConicalIcon;
  let activeLocatorId: string | null = null;
  if (activeCitation) {
    try {
      activeLocatorId = citationLocatorId(activeCitation.evidenceUrl);
    } catch {
      activeLocatorId = null;
    }
  }
  return (
    <section
      aria-label={card.title}
      className={cn(
        "rounded-xl border bg-card/50",
        limited ? "border-chart-3/40" : "border-chart-1/40",
      )}
    >
      <div className="flex items-center gap-2 px-4 py-2.5">
        <Icon
          aria-hidden="true"
          className={cn(
            "size-4 shrink-0",
            limited ? "text-chart-3" : "text-chart-1",
          )}
        />
        <span
          className={cn(
            "font-mono text-[11px] tracking-widest",
            limited ? "text-chart-3" : "text-chart-1",
          )}
        >
          {limited ? copy.result.limitedBadge : copy.result.normalBadge}
        </span>
        <p className="min-w-0 truncate text-sm font-medium text-foreground">
          {card.title}
        </p>
      </div>

      <div className="px-4 pb-1">
        {card.layers.map((layer, i) => (
          <div key={i} className="border-t border-border py-3.5">
            <p className="font-mono text-[11px] tracking-widest text-muted-foreground">
              {copy.result.layers[i]}
            </p>
            <div
              className={cn(
                "mt-1.5 space-y-2 leading-6",
                i === 0
                  ? "text-[15px] text-foreground"
                  : "text-sm text-muted-foreground",
              )}
            >
              {layer.body.map((line, j) => (
                <MessageResponse key={j}>{line}</MessageResponse>
              ))}
            </div>
            {i === 1 && citations.length > 0 && (
              <div className="mt-3">
                <p className="font-mono text-[10px] tracking-widest text-muted-foreground">
                  {copy.result.exactCitations}
                </p>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {citations.map((citation, index) => (
                    <EvidenceCitation
                      key={citation.citationId}
                      citation={citation}
                      ordinal={index + 1}
                      active={activeCitation?.citationId === citation.citationId}
                      onOpen={(value) => setActiveCitation((current) =>
                        current?.citationId === value.citationId ? null : value
                      )}
                    />
                  ))}
                </div>
                {activeCitation && activeLocatorId && (
                  <div className="mt-2 rounded-md border border-chart-1/30 bg-background/60 p-2">
                    <MaterialPreview
                      key={activeCitation.citationId}
                      researchId={card.researchId}
                      materialId={activeCitation.materialId}
                      initialLocatorId={activeLocatorId}
                    />
                  </div>
                )}
              </div>
            )}
            {i === 1 && <SourceUseSummary sources={sourceUse} />}
            {i === 1 && officialSources.length > 0 && (
              <div className="mt-3">
                <p className="font-mono text-[10px] tracking-widest text-muted-foreground">
                  核对官方来源
                </p>
                <ul className="mt-1.5 space-y-1.5">
                  {officialSources.map((source) => (
                    <li key={source.sourceId}>
                      <a
                        className="text-chart-1 underline decoration-chart-1/40 underline-offset-4 hover:decoration-chart-1"
                        href={source.finalUrl}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {source.sourceId} · {source.parseLocation}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {i === lastLayer && card.attachments && card.attachments.length > 0 && (
              <AttachmentCard items={card.attachments} />
            )}
          </div>
        ))}
      </div>

      <p className="border-t border-border px-4 py-2 font-mono text-[11px] text-muted-foreground/70">
        {copy.result.recordNote}
      </p>
    </section>
  );
}
