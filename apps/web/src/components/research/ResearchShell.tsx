"use client";

/**
 * 研究页容器：只做组装。
 * 状态与副作用分别在两个 hook 中——
 * - useResearchMessages：消息流 / 卡片状态与快照恢复
 * - useJobPoller：注册表条目、2s 轮询循环、任务动作
 * 这里只保留输入分发的薄包装（追问卡回答 / 建议 chips / 责任卡选择）与渲染。
 */
import Link from "next/link";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { copy } from "@/lib/copy";
import { cn } from "@/lib/utils";
import { isTerminal } from "@/lib/d18/map";
import { useJobPoller } from "@/lib/use-job-poller";
import { useResearchMessages } from "@/lib/use-research-messages";
import { Composer } from "@/components/chat/Composer";
import { MessageList } from "@/components/chat/MessageList";
import { TaskPanel } from "@/components/chat/TaskPanel";
import { TopBar } from "@/components/chat/TopBar";
import type { Suggestion } from "@/components/chat/types";
import type { ComposerSubmission } from "@/lib/d18/material-types";

/** ≥xl 媒体查询：右侧面板的默认开合跟随断点（SSR 快照恒 false，挂载后校正） */
function useIsXl(): boolean {
  return useSyncExternalStore(
    (onStoreChange) => {
      const mq = window.matchMedia("(min-width: 1280px)");
      mq.addEventListener("change", onStoreChange);
      return () => mq.removeEventListener("change", onStoreChange);
    },
    () => window.matchMedia("(min-width: 1280px)").matches,
    () => false,
  );
}

export function ResearchShell({ researchId }: { researchId: string }) {
  const messagesApi = useResearchMessages(researchId);
  const {
    entry,
    missing,
    job,
    statusText,
    todo,
    busy,
    materialMode,
    setMaterialMode,
    submitObjective,
    submitInput,
    doCancel,
    doResume,
  } = useJobPoller({ researchId, messages: messagesApi });

  // 右侧研究详情面板：null = 跟随断点（≥xl 默认开，以下默认关）；
  // ≥xl 为内联右栏，<xl 同一状态驱动右侧抽屉（原窄屏顶条逻辑收敛至此）
  const [panelOverride, setPanelOverride] = useState<boolean | null>(null);
  const isXl = useIsXl();
  const panelOpen = panelOverride ?? isXl;

  // Esc 关闭抽屉（仅 <xl 抽屉态有意义；内联态按 Esc 同样收起，再按断点恢复）
  useEffect(() => {
    if (!panelOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPanelOverride(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [panelOpen]);

  // —— 建议 chips：「我来补充」聚焦输入区；「没有这份材料」走 decision 通道 ——
  const pickSuggestion = useCallback(
    (s: Suggestion) => {
      if (s.intent === "supply") {
        setMaterialMode(true);
        messagesApi.appendAssistant(copy.research.materialPrompt);
      } else if (s.intent === "decline") {
        void submitInput(
          "decision",
          {
            decision_id: `${job?.job_id ?? "job"}:decline:${entry?.inputSeq ?? 0}`,
            decision: "unavailable",
          },
          copy.research.declineEcho,
          { noteAfter: copy.research.declineNote },
        );
      }
    },
    [entry?.inputSeq, job?.job_id, messagesApi, setMaterialMode, submitInput],
  );

  const responsibilityChoice = useCallback(
    (_messageId: string, choice: string) => {
      const materialRecovery =
        job?.input_required?.interaction_kind === "material_processing_recovery";
      const decision = materialRecovery
        ? choice === copy.research.responsibilityAgree
          ? "retry"
          : "omit_failed_materials"
        : choice;
      const visibleChoice = materialRecovery
        ? decision === "retry"
          ? copy.research.materialRecoveryRetry
          : copy.research.materialRecoveryOmit
        : choice;
      void submitInput(
        "decision",
        {
          decision_id: `${job?.job_id ?? "job"}:confirm:${entry?.inputSeq ?? 0}`,
          decision,
        },
        `${copy.research.decisionEcho}${visibleChoice}`,
        { cardChoice: choice },
      );
    },
    [
      entry?.inputSeq,
      job?.input_required?.interaction_kind,
      job?.job_id,
      submitInput,
    ],
  );

  // 追问卡内的自由文本是新的用户轮次，不伪装成材料附件。
  const submitUserTurn = useCallback(
    (text: string) => {
      void submitObjective(text);
    },
    [submitObjective],
  );

  const sendText = useCallback(
    (submission: ComposerSubmission) => {
      const text = submission.text;
      if (materialMode && job) {
        submitUserTurn(text);
        return;
      }
      void submitObjective(text, submission.materialIds);
    },
    [job, materialMode, submitObjective, submitUserTurn],
  );

  if (missing) {
    return (
      <div className="flex h-dvh flex-col items-center justify-center gap-3">
        <p className="text-sm text-muted-foreground">{copy.research.missing}</p>
        <Link
          href="/projects"
          className="font-mono text-[11px] text-muted-foreground underline-offset-4 hover:text-chart-1 hover:underline"
        >
          ‹ {copy.research.backToProjects}
        </Link>
      </div>
    );
  }

  // 注册表/消息未水合：渲染与最终布局同形的骨架，避免闪烁
  if (!entry) {
    return (
      <div
        className="flex h-dvh flex-col"
        aria-busy="true"
        aria-label={copy.research.loading}
      >
        <div className="h-12 shrink-0 border-b border-border" />
        <div className="mx-auto w-full max-w-3xl flex-1 space-y-6 px-4 py-6 sm:px-6">
          <div className="ml-auto h-9 w-2/5 animate-pulse rounded-2xl bg-muted" />
          <div className="h-4 w-3/4 animate-pulse rounded bg-muted" />
          <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
          <div className="h-24 w-full animate-pulse rounded-xl bg-muted" />
        </div>
        <div className="px-4 pb-5 pt-1 sm:px-6">
          <div className="mx-auto min-h-[118px] w-full max-w-3xl animate-pulse rounded-2xl bg-muted" />
        </div>
      </div>
    );
  }

  const waiting = job?.status === "waiting_for_input";
  const active = job ? !isTerminal(job.status) && !waiting : false;

  const taskPanelProps = {
    researchId,
    goal: entry.objective,
    materials: entry.materials.map((m, i) => ({
      id: `m-${i}`,
      name: m.name,
      kind: copy.research.materialKind,
      note: m.note,
    })),
    status: statusText,
    todo,
  };

  const suggestions =
    waiting && job?.input_required && !job.input_required.authorization_required
      ? [
          {
            id: "s-supply",
            label: copy.research.suggestSupply,
            intent: "supply" as const,
          },
          {
            id: "s-decline",
            label: copy.research.suggestDecline,
            intent: "decline" as const,
          },
        ]
      : [];

  return (
    <div className="flex h-dvh flex-col">
      <TopBar
        taskTitle={entry.title}
        statusLabel={job?.status ?? null}
        canCancel={active || waiting}
        canResume={waiting}
        onCancel={doCancel}
        onResume={doResume}
        busy={busy}
        panelOpen={panelOpen}
        onTogglePanel={() => setPanelOverride(!panelOpen)}
      />

      <div className="relative flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col">
          <MessageList
            messages={messagesApi.messages}
            suggestions={suggestions}
            onPickSuggestion={pickSuggestion}
            onResponsibilityChoice={responsibilityChoice}
            onGapAnswer={submitUserTurn}
          />
          <Composer
            disabled={busy}
            inputDisabled={active}
            running={active}
            draftId={researchId}
            onSend={sendText}
            onStop={doCancel}
            placeholder={
              materialMode
                ? copy.research.composerMaterialPlaceholder
                : active || waiting
                  ? copy.research.midRunUnavailablePlaceholder
                  : entry.jobId
                    ? copy.research.continuationPlaceholder
                  : copy.research.objectivePlaceholder
            }
          />
        </main>

        {/* ≥xl：内联右栏 */}
        {panelOpen && (
          <aside className="hidden w-80 shrink-0 border-l border-border xl:block">
            <TaskPanel {...taskPanelProps} />
          </aside>
        )}

        {/* <xl：右侧抽屉（遮罩点击收起） */}
        {panelOpen && <div
          className="absolute inset-0 z-40 xl:hidden"
        >
          <div
            className={cn(
              "absolute inset-0 bg-black/50 transition-opacity duration-200",
              "opacity-100",
            )}
            onClick={() => setPanelOverride(false)}
          />
          <aside
            className={cn(
              "absolute inset-y-0 right-0 w-80 max-w-[85vw] border-l border-border bg-background transition-transform duration-200 ease-out",
              "translate-x-0",
            )}
          >
            <TaskPanel {...taskPanelProps} />
          </aside>
        </div>}
      </div>
    </div>
  );
}
