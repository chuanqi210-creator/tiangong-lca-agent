"use client";

/**
 * 研究页顶栏：窄屏汉堡按钮（唤起侧栏抽屉）+ 可改名标题（双击，
 * 接 registry.renameResearch）+ 状态徽标（沿用 map.ts jobStatusBadge，
 * 视觉走语义 token）；右侧为取消/恢复 action 区（带 tooltip）。
 * props 接口与原实现一致，ResearchShell 调用处无需改动。
 */
import { usePathname } from "next/navigation";
import { Menu, PanelRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { copy } from "@/lib/copy";
import { isTerminal, jobStatusBadge } from "@/lib/d18/map";
import type { JobStatus } from "@/lib/d18/types";
import { renameResearch } from "@/lib/registry";
import { formatElapsed } from "@/lib/time";
import { useResearchEntry } from "@/lib/use-registry";
import { cn } from "@/lib/utils";
import { useAppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** jobStatusBadge 的旧 tone 名 → 语义 token 色 */
const toneClass = {
  phos: "text-chart-1",
  amber: "text-chart-5",
  dim: "text-muted-foreground",
  bad: "text-destructive",
} as const;

export function TopBar({
  taskTitle,
  statusLabel,
  canCancel,
  canResume,
  onCancel,
  onResume,
  busy,
  panelOpen,
  onTogglePanel,
}: {
  taskTitle: string;
  statusLabel: JobStatus | null;
  canCancel: boolean;
  canResume: boolean;
  onCancel: () => void;
  onResume: () => void;
  busy: boolean;
  /** 右侧研究详情面板当前是否展开（驱动按钮高亮态） */
  panelOpen: boolean;
  onTogglePanel: () => void;
}) {
  const { openSidebar } = useAppShell();
  const pathname = usePathname();
  const researchId = pathname.startsWith("/research/")
    ? (pathname.split("/")[2] ?? null)
    : null;
  // 注册表是标题的唯一事实源：改名后立即反映，无需惊动 ResearchShell
  const liveEntry = useResearchEntry(researchId);
  const title = liveEntry?.title ?? taskTitle;

  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  const startEditing = () => {
    if (!researchId) return;
    setEditValue(title);
    setEditing(true);
  };

  const confirmRename = () => {
    setEditing(false);
    const trimmed = editValue.trim();
    if (researchId && trimmed && trimmed !== title) {
      renameResearch(researchId, trimmed);
    }
  };

  const badge = statusLabel ? jobStatusBadge[statusLabel] : null;

  // 任务已进行时间：registry createdAt 起算，分钟级刷新；终态后隐藏
  const showElapsed =
    !!liveEntry?.jobId && !!statusLabel && !isTerminal(statusLabel);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!showElapsed) return;
    const id = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(id);
  }, [showElapsed]);

  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-background px-3">
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              className="md:hidden"
              onClick={openSidebar}
              aria-label={copy.topbar.openSidebar}
            />
          }
        >
          <Menu className="size-4" />
        </TooltipTrigger>
        <TooltipContent side="bottom">{copy.topbar.openSidebar}</TooltipContent>
      </Tooltip>

      {editing ? (
        <form
          className="min-w-0 flex-1"
          onSubmit={(e) => {
            e.preventDefault();
            confirmRename();
          }}
        >
          <input
            ref={inputRef}
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={confirmRename}
            onKeyDown={(e) => {
              if (e.key === "Escape") setEditing(false);
            }}
            aria-label={copy.sidebar.rename}
            className="h-7 w-full max-w-md rounded-md border border-ring bg-transparent px-2 text-sm outline-none"
          />
        </form>
      ) : (
        <Tooltip>
          <TooltipTrigger
            render={
              <h1
                className="min-w-0 flex-1 cursor-default truncate text-sm font-medium"
                onDoubleClick={startEditing}
              />
            }
          >
            {title}
          </TooltipTrigger>
          <TooltipContent side="bottom">{copy.topbar.renameHint}</TooltipContent>
        </Tooltip>
      )}

      <div className="flex shrink-0 items-center gap-1.5">
        {showElapsed && liveEntry && (
          <span className="hidden font-mono text-xs text-muted-foreground tabular-nums sm:inline">
            {copy.topbar.elapsedLabel} {formatElapsed(liveEntry.createdAt, now)}
          </span>
        )}
        {badge && (
          <span
            className={cn(
              "flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 font-mono text-[11px]",
              toneClass[badge.tone],
            )}
          >
            <span
              className={cn(
                "size-1.5 rounded-full bg-current",
                statusLabel && !isTerminal(statusLabel) && "animate-pulse",
              )}
            />
            {badge.label}
          </span>
        )}
        {canResume && (
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onResume}
                  disabled={busy}
                />
              }
            >
              {copy.research.resume}
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {copy.topbar.resumeHint}
            </TooltipContent>
          </Tooltip>
        )}
        {canCancel && (
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onCancel}
                  disabled={busy}
                  className="text-muted-foreground hover:text-destructive"
                />
              }
            >
              {copy.research.cancel}
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {copy.topbar.cancelHint}
            </TooltipContent>
          </Tooltip>
        )}
        <span
          aria-hidden
          className="mx-1 h-4 w-px shrink-0 bg-border"
        />
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={onTogglePanel}
                aria-label={copy.topbar.panelToggle}
                aria-pressed={panelOpen}
                className={cn(
                  "text-muted-foreground",
                  panelOpen && "bg-muted text-foreground",
                )}
              />
            }
          >
            <PanelRight className="size-4" />
          </TooltipTrigger>
          <TooltipContent side="bottom">{copy.topbar.panelToggle}</TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
