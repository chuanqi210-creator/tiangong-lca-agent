"use client";

import { ShieldAlertIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { copy } from "@/lib/copy";
import type { ResponsibilityCardData } from "@/components/chat/types";

/**
 * 责任确认卡（视觉参考 palot chat-permission）：
 * ShieldAlert + "需要你的确认"语义，说明将执行什么/影响什么；
 * 同意（primary）/ 暂不（outline），handled 态保留并禁用交互。
 */
export function ResponsibilityCard({
  card,
  handled,
  choice,
  onChoice,
}: {
  card: ResponsibilityCardData;
  handled: boolean;
  choice?: string;
  onChoice: (choice: string) => void;
}) {
  return (
    <section
      aria-label={card.title}
      className="rounded-xl border border-border bg-card/50"
    >
      <div className="px-3 py-2.5">
        <div className="flex items-center gap-1.5">
          <ShieldAlertIcon
            className="size-4 shrink-0 text-chart-3"
            aria-hidden="true"
          />
          <span className="text-sm font-medium text-foreground">
            {card.title}
          </span>
        </div>
        <div className="mt-2.5 space-y-2.5 text-sm leading-6">
          <div>
            <p className="text-xs text-muted-foreground">
              {copy.research.responsibilityActionLabel}
            </p>
            <p className="mt-0.5 text-foreground">{card.action}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">
              {copy.research.responsibilityImpactLabel}
            </p>
            <p className="mt-0.5 text-muted-foreground">{card.impact}</p>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-3 py-2">
        {handled ? (
          <p className="text-xs text-muted-foreground">
            {copy.research.responsibilityChosen}
            {choice}
          </p>
        ) : (
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => onChoice(copy.research.responsibilityDecline)}
            >
              {card.declineLabel ?? copy.research.responsibilityDecline}
            </Button>
            <Button
              size="sm"
              onClick={() => onChoice(copy.research.responsibilityAgree)}
            >
              {card.agreeLabel ?? copy.research.responsibilityAgree}
            </Button>
          </>
        )}
      </div>
    </section>
  );
}
