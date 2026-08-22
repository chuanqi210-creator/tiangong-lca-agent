"use client";

import { InfoIcon, TriangleAlertIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { NoticeCardData } from "@/components/chat/types";

/** 可恢复提示条：shadcn 风格（border + muted 背景 + lucide 图标按 tone） */
export function NoticeCard({ card }: { card: NoticeCardData }) {
  const recoverable = card.tone === "recoverable";
  const Icon = recoverable ? TriangleAlertIcon : InfoIcon;
  return (
    <div className="flex items-start gap-3 rounded-lg border border-border bg-muted/50 px-4 py-3">
      <Icon
        aria-hidden="true"
        className={cn(
          "mt-0.5 size-4 shrink-0",
          recoverable ? "text-chart-3" : "text-muted-foreground",
        )}
      />
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{card.title}</p>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          {card.body}
        </p>
      </div>
    </div>
  );
}
