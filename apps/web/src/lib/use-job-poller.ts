/**
 * 研究页任务轮询与任务动作 hook（自 ResearchShell 原样拆出）：
 * - 注册表条目（entry）与持久化 persist、事件游标 cursorRef
 * - 2s 轮询循环（getJob + pullEvents + 游标管理，POLL_MS 语义不变）
 * - 提交研究目标 / 补充输入 / 取消 / 恢复等任务动作
 * 消息流与卡片的渲染状态全部委托给 useResearchMessages。
 * 轮询/提交/取消失败时追加 toast 提醒（首轮连续失败才提示，避免刷屏）。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { copy } from "@/lib/copy";
import {
  cancelJob,
  createContinuation,
  createJob,
  fetchEvents,
  getJob,
  recoverResearch,
  resumeJob,
  supplyInput,
} from "@/lib/d18/client";
import {
  gapFromInputRequired,
  isTerminal,
  phaseFromJob,
  type ResearchPhase,
} from "@/lib/d18/map";
import type { JobView } from "@/lib/d18/types";
import { pendingMaterialsAreComplete } from "@/lib/d18/registry-storage";
import { ensureD18Session } from "@/lib/session-client";
import {
  clearPendingSourceMaterials,
  getPendingSourceMaterials,
  getResearch,
  restoreResearchFromAuthority,
  updateResearch,
  type ResearchEntry,
} from "@/lib/registry";
import { useToast } from "@/components/ui/toast";
import type { TodoState } from "@/components/chat/TaskPanel";
import type { ResearchMessagesApi } from "@/lib/use-research-messages";

const POLL_MS = 2000;

function phaseStatusText(phase: ResearchPhase): string {
  return copy.research.statusText[phase];
}

export function useJobPoller({
  researchId,
  messages: messagesApi,
}: {
  researchId: string;
  messages: ResearchMessagesApi;
}) {
  const {
    restore,
    applyEvents,
    closeNarrative,
    appendUser,
    appendAssistant,
    ensureGapCard,
    settleResult,
    appendFailedNotice,
    appendCancelledNote,
    appendInputFailedNotice,
    markGapHandled,
    beginResultRevision,
  } = messagesApi;

  const { toast } = useToast();

  const [entry, setEntry] = useState<ResearchEntry | null>(null);
  const [job, setJob] = useState<JobView | null>(null);
  const [todo, setTodo] = useState<TodoState>({
    mode: "agent-working",
    text: copy.research.todoIdle,
  });
  const [statusText, setStatusText] = useState<string>(
    copy.research.statusText.idle,
  );
  const [busy, setBusy] = useState(false);
  const [materialMode, setMaterialMode] = useState(false);
  const [missing, setMissing] = useState(false);

  const entryRef = useRef<ResearchEntry | null>(null);
  const jobRef = useRef<JobView | null>(null);
  const cursorRef = useRef(0);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sendingRef = useRef(false);
  const pollFailRef = useRef(0);

  const persist = useCallback(
    (patch: Partial<ResearchEntry>) => {
      const next = updateResearch(researchId, patch);
      if (next) {
        entryRef.current = next;
        setEntry(next);
      }
    },
    [researchId],
  );

  // —— 事件增量：游标推进 + 叙述持久化在此，UI 转换委托给 messages hook ——
  const pullEvents = useCallback(async () => {
    const jobId = entryRef.current?.jobId;
    if (!jobId) return;
    try {
      const { events, cursor } = await fetchEvents(jobId, cursorRef.current);
      if (events.length === 0) return;
      cursorRef.current = cursor;
      applyEvents(events);
      const e = entryRef.current;
      if (e) {
        persist({
          lastEventCursor: cursor,
          narrative: [
            ...e.narrative,
            ...events.map((ev) => ({
              sequence: ev.sequence,
              label: ev.label,
              status: ev.status,
            })),
          ],
        });
      }
    } catch {
      /* 事件拉取失败等下一轮 */
    }
  }, [applyEvents, persist]);

  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  // —— 状态同步：getJob 驱动相位与卡片 ——
  const syncJob = useCallback(async () => {
    const jobId = entryRef.current?.jobId;
    if (!jobId) return;
    try {
      const view = await getJob(jobId);
      jobRef.current = view;
      setJob(view);
      persist({ status: view.status });
      await pullEvents();
      pollFailRef.current = 0;
      const phase = phaseFromJob(view);
      setStatusText(phaseStatusText(phase));

      if (view.status === "waiting_for_input" && view.input_required) {
        const gap = gapFromInputRequired(view.input_required);
        closeNarrative();
        setTodo({ mode: "need-user", text: gap.question });
        ensureGapCard(view, gap);
        stopPolling();
        return;
      }

      if (isTerminal(view.status)) {
        stopPolling();
        closeNarrative();
        if (view.status === "completed" || view.status === "bounded") {
          const ok = await settleResult(view);
          if (ok) setTodo({ mode: "agent-working", text: copy.research.todoDone });
        } else if (view.status === "failed") {
          appendFailedNotice(view);
          setTodo({ mode: "agent-working", text: phaseStatusText(phase) });
        } else if (view.status === "cancelled") {
          appendCancelledNote();
          setTodo({ mode: "agent-working", text: copy.research.todoCancelled });
        }
      } else {
        setTodo({ mode: "agent-working", text: phaseStatusText(phase) });
      }
    } catch {
      /* 状态拉取失败等下一轮；首轮连续失败提醒一次 */
      pollFailRef.current += 1;
      if (pollFailRef.current === 1) toast(copy.toast.pollFailed, "error");
    }
  }, [
    appendCancelledNote,
    appendFailedNotice,
    closeNarrative,
    ensureGapCard,
    persist,
    pullEvents,
    settleResult,
    stopPolling,
    toast,
  ]);

  const startPolling = useCallback(() => {
    stopPolling();
    pollingRef.current = setInterval(() => void syncJob(), POLL_MS);
  }, [stopPolling, syncJob]);

  // —— 首轮研究目标 → 创建任务（幂等键复用，发送中禁用重复提交）——
  const submitObjective = useCallback(
    async (text: string, materialIds: string[] = []) => {
      const e = entryRef.current;
      if (!e || sendingRef.current) return;
      sendingRef.current = true;
      setBusy(true);
      try {
        if (!e.jobId) {
          const session = await ensureD18Session();
          if (!researchId.startsWith(`${session.projectNamespace}-`)) {
            throw new Error("research project is outside the active session scope");
          }
          const pendingMaterials = getPendingSourceMaterials(researchId);
          const expectedMaterials = e.pendingSourceMaterialCount ?? 0;
          if (!pendingMaterialsAreComplete(expectedMaterials, pendingMaterials.length)) {
            toast(copy.toast.materialsNeedReattach, "error");
            return;
          }
          appendUser(text);
          persist({ userMessages: [...e.userMessages, text] });
          const created = await createJob(
            researchId,
            e.objective,
            e.idempotencyKey,
            pendingMaterials,
            materialIds.length > 0 ? materialIds : (e.pendingMaterialIds ?? []),
          );
          clearPendingSourceMaterials(researchId);
          persist({
            jobId: created.job_id,
            status: created.status,
            pendingSourceMaterialCount: 0,
            wireObjective: undefined,
            pendingMaterialIds: undefined,
          });
          setStatusText(copy.research.statusText.working);
          setTodo({ mode: "agent-working", text: copy.research.statusText.working });
          await syncJob();
          startPolling();
        } else {
          const priorJob = jobRef.current;
          if (
            !priorJob ||
            (!isTerminal(priorJob.status) &&
              priorJob.status !== "waiting_for_input")
          ) {
            toast(copy.toast.followupUnavailable, "error");
            return;
          }
          const inputId = `${priorJob.job_id}:turn:${e.inputSeq}`;
          const continued = await createContinuation(
            priorJob.job_id,
            inputId,
            text,
          );
          beginResultRevision();
          appendUser(text);
          appendAssistant(
            copy.research.continuationAccepted,
            "backend_input_receipt",
          );
          cursorRef.current = 0;
          persist({
            jobId: continued.job_id,
            status: continued.status,
            inputSeq: e.inputSeq + 1,
            userMessages: [...e.userMessages, text],
            lastEventCursor: 0,
          });
          jobRef.current = {
            job_id: continued.job_id,
            status: continued.status,
            project_id: continued.project_id,
          };
          setJob(jobRef.current);
          setStatusText(copy.research.statusText.working);
          setTodo({
            mode: "agent-working",
            text: copy.research.statusText.working,
          });
          await syncJob();
          startPolling();
        }
      } catch {
        toast(copy.toast.submitFailed, "error");
      } finally {
        sendingRef.current = false;
        setBusy(false);
      }
    },
    [
      appendAssistant,
      appendUser,
      beginResultRevision,
      persist,
      researchId,
      startPolling,
      syncJob,
      toast,
    ],
  );

  // —— 进入研究页：从注册表恢复（刷新可恢复；异步触发避免 effect 内同步 setState）——
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      void (async () => {
        let stored = getResearch(researchId);
        if (!stored) {
          try {
            const session = await ensureD18Session();
            if (!researchId.startsWith(`${session.projectNamespace}-`)) {
              throw new Error(
                "research project is outside the active session scope",
              );
            }
            stored = restoreResearchFromAuthority(
              await recoverResearch(researchId),
            );
          } catch (error) {
            if (cancelled) return;
            const status = (error as Error & { status?: number }).status;
            if (status === 404) setMissing(true);
            else toast(copy.toast.pollFailed, "error");
            return;
          }
        }
        if (cancelled) return;
        entryRef.current = stored;
        setEntry(stored);
        cursorRef.current = stored.lastEventCursor;

        // 恢复对话流：用户消息 + 已沉淀叙述 + 卡片快照
        restore(stored);

        if (stored.jobId) {
          void syncJob().then(() => {
            const view = jobRef.current;
            if (
              view &&
              !isTerminal(view.status) &&
              view.status !== "waiting_for_input"
            ) {
              startPolling();
            }
          });
        } else if (stored.objective) {
          // 新建研究进入：首轮研究目标自动创建任务
          void submitObjective(stored.objective);
        } else {
          setStatusText(copy.research.statusText.idle);
          setTodo({ mode: "agent-working", text: copy.research.todoIdle });
        }
      })();
    }, 0);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      stopPolling();
    };
  }, [
    researchId,
    restore,
    startPolling,
    stopPolling,
    submitObjective,
    syncJob,
    toast,
  ]);

  // —— 补充输入（material / decision），input_id 按 inputSeq 幂等 ——
  const submitInput = useCallback(
    async (
      kind: "material" | "decision",
      payload: Record<string, unknown>,
      echo: string,
      opts?: { materialName?: string; noteAfter?: string; cardChoice?: string },
    ) => {
      const e = entryRef.current;
      const view = jobRef.current;
      if (!e || !view || sendingRef.current) return;
      sendingRef.current = true;
      setBusy(true);
      try {
        const inputId = `${view.job_id}:input:${e.inputSeq}`;
        try {
          await supplyInput(view.job_id, inputId, kind, payload);
        } catch {
          appendInputFailedNotice(inputId);
          toast(copy.toast.submitFailed, "error");
          return;
        }
        persist({ inputSeq: e.inputSeq + 1 });
        markGapHandled(view.job_id, opts?.cardChoice ?? echo);
        appendUser(echo);
        if (opts?.noteAfter) {
          appendAssistant(opts.noteAfter, "backend_input_receipt");
        }
        persist({ userMessages: [...e.userMessages, echo] });
        if (kind === "material") {
          persist({
            materials: [
              ...e.materials,
              {
                name: opts?.materialName ?? echo,
                note: copy.research.materialNote,
              },
            ],
          });
        }
        setTodo({ mode: "agent-working", text: copy.research.statusText.working });
        setStatusText(copy.research.statusText.working);
        setMaterialMode(false);
        await syncJob();
        startPolling();
      } finally {
        sendingRef.current = false;
        setBusy(false);
      }
    },
    [
      appendAssistant,
      appendInputFailedNotice,
      appendUser,
      markGapHandled,
      persist,
      startPolling,
      syncJob,
      toast,
    ],
  );

  const doCancel = useCallback(async () => {
    const view = jobRef.current;
    if (!view || sendingRef.current) return;
    sendingRef.current = true;
    setBusy(true);
    try {
      await cancelJob(view.job_id);
      await syncJob();
    } catch {
      toast(copy.toast.cancelFailed, "error");
    } finally {
      sendingRef.current = false;
      setBusy(false);
    }
  }, [syncJob, toast]);

  const doResume = useCallback(async () => {
    const view = jobRef.current;
    if (!view || sendingRef.current) return;
    sendingRef.current = true;
    setBusy(true);
    try {
      await resumeJob(view.job_id);
      setStatusText(copy.research.statusText.working);
      await syncJob();
      startPolling();
    } finally {
      sendingRef.current = false;
      setBusy(false);
    }
  }, [startPolling, syncJob]);

  return {
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
  };
}
