/**
 * 研究页消息流状态 hook（自 ResearchShell 原样拆出）：
 * - UIMessage[] 状态与事件 → 工作叙述的转换（按 sequence 由调用方去重）
 * - 追问/责任/结果/提示卡的生成、handled 标记与去重
 * - 卡片快照持久化：卡片产生 / handled 变化时全量覆写 localStorage 快照，
 *   restore() 时先水合快照渲染，再由轮询增量校正
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { copy } from "@/lib/copy";
import { failedNoticeFromJob } from "@/lib/d18/map";
import { getResult } from "@/lib/d18/client";
import { resultLayers } from "@/lib/d18/map";
import { replaceWithAuthoritativeResult } from "@/lib/d18/result-authority";
import type {
  D18Attachment,
  JobEventData,
  JobView,
  ResultView,
} from "@/lib/d18/types";
import {
  readCardSnapshots,
  writeCardSnapshots,
  type ResearchEntry,
} from "@/lib/registry";
import type { UIMessage } from "@/components/chat/types";

let seq = 0;
const nid = () => `m${++seq}`;
const RESULT_RETRY_DELAYS_MS = [0, 250, 750, 1500] as const;

function waitForResultRetry(delayMs: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

export interface GapInfo {
  authorizationRequired: boolean;
  question: string;
  interactionKind: string;
}

export function useResearchMessages(researchId: string) {
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const resultLoadedRef = useRef(false);
  const hydratedRef = useRef(false);

  // —— 卡片快照持久化：水合完成后，任何卡片产生 / handled 变化都会全量覆写 ——
  useEffect(() => {
    if (!hydratedRef.current) return;
    writeCardSnapshots(
      researchId,
      messages
        .filter((m): m is Extract<UIMessage, { kind: "card" }> => m.kind === "card")
        .map((m) => ({ card: m.card, handled: m.handled, choice: m.choice })),
    );
  }, [messages, researchId]);

  // —— 刷新恢复：用户消息 + 已沉淀叙述 + 卡片快照 ——
  const restore = useCallback((stored: ResearchEntry) => {
    const restored: UIMessage[] = [];
    for (const text of stored.userMessages) {
      restored.push({ id: nid(), kind: "user", text });
    }
    if (stored.narrative.length > 0) {
      restored.push({
        id: nid(),
        kind: "narrative",
        items: stored.narrative.map((n) => ({ text: n.label, done: true })),
        active: false,
      });
    }
    for (const snap of readCardSnapshots(stored.researchId)) {
      restored.push({
        id: nid(),
        kind: "card",
        card: snap.card,
        handled: snap.handled,
        choice: snap.choice,
      });
    }
    // 快照只作乐观显示；终态轮询仍必须从 Project Record 结果端点重拉。
    resultLoadedRef.current = false;
    hydratedRef.current = true;
    setMessages(restored);
  }, []);

  // —— 事件增量 → 工作叙述 ——
  const applyEvents = useCallback((events: JobEventData[]) => {
    if (events.length === 0) return;
    setMessages((prev) => {
      let msgs = prev;
      for (const evt of events) {
        const last = msgs[msgs.length - 1];
        if (last?.kind === "narrative" && last.active) {
          msgs = [
            ...msgs.slice(0, -1),
            {
              ...last,
              items: [
                ...last.items.map((it) => ({ ...it, done: true })),
                { text: evt.label, done: false },
              ],
            },
          ];
        } else {
          msgs = [
            ...msgs.map((m) =>
              m.kind === "narrative" && m.active
                ? {
                    ...m,
                    active: false,
                    items: m.items.map((it) => ({ ...it, done: true })),
                  }
                : m,
            ),
            {
              id: nid(),
              kind: "narrative",
              items: [{ text: evt.label, done: false }],
              active: true,
            },
          ];
        }
      }
      return msgs;
    });
  }, []);

  const closeNarrative = useCallback(() => {
    setMessages((prev) =>
      prev.map((m) =>
        m.kind === "narrative" && m.active
          ? { ...m, active: false, items: m.items.map((it) => ({ ...it, done: true })) }
          : m,
      ),
    );
  }, []);

  const appendUser = useCallback((text: string) => {
    setMessages((prev) => [...prev, { id: nid(), kind: "user", text }]);
  }, []);

  const appendAssistant = useCallback((
    text: string,
    provenance: "backend_input_receipt" | "local_ui_notice" = "local_ui_notice",
  ) => {
    setMessages((prev) => [
      ...prev,
      { id: nid(), kind: "assistant", text, streaming: false, provenance },
    ]);
  }, []);

  // —— waiting_for_input → 追问卡 / 责任卡（同一张未处理卡不重复入列）——
  const ensureGapCard = useCallback((view: JobView, gap: GapInfo) => {
    setMessages((prev) => {
      if (
        prev.some(
          (m) => m.kind === "card" && m.card.id === `gap-${view.job_id}` && !m.handled,
        )
      ) {
        return prev;
      }
      const cardMsg: UIMessage = gap.authorizationRequired
        ? {
            id: nid(),
            kind: "card",
            card: {
              kind: "responsibility",
              id: `gap-${view.job_id}`,
              title: copy.research.responsibilityTitle,
              action: gap.question,
              impact:
                gap.interactionKind === "material_processing_recovery"
                  ? copy.research.materialRecoveryImpact
                  : copy.research.responsibilityImpact,
              agreeLabel:
                gap.interactionKind === "material_processing_recovery"
                  ? copy.research.materialRecoveryRetry
                  : undefined,
              declineLabel:
                gap.interactionKind === "material_processing_recovery"
                  ? copy.research.materialRecoveryOmit
                  : undefined,
            },
            handled: false,
          }
        : {
            id: nid(),
            kind: "card",
            card: {
              kind: "gap_question",
              id: `gap-${view.job_id}`,
              title: copy.research.gapTitle,
              why: gap.question,
              accept: copy.research.gapAccept,
            },
            handled: false,
          };
      return [...prev, cardMsg];
    });
  }, []);

  // —— 终态：拉结果视图 → 结果卡（含成果附件映射）。
  // 刷新后若快照已含水合的结果卡则跳过；live 模式快照缺失时此处即 /result 重拉兜底 ——
  const settleResult = useCallback(async (view: JobView): Promise<boolean> => {
    if (resultLoadedRef.current) return true;
    resultLoadedRef.current = true;
    for (const delayMs of RESULT_RETRY_DELAYS_MS) {
      if (delayMs > 0) await waitForResultRetry(delayMs);
      try {
        const result: ResultView = await getResult(view.job_id);
        const {
          limited,
          layers,
          citations,
          officialSources,
          sourceUse,
          materialLimitations,
        } = resultLayers(result);
        // 成果附件：result 视图附带的宽容字段（fixture 模式由引擎下发；live 模式暂无此字段）
        const attachments = (
          result as ResultView & { attachments?: D18Attachment[] }
        ).attachments?.map((a) => ({
          fileUrl: a.fileUrl,
          fileName: a.fileName,
          fileType: a.fileType,
          mimeType: a.mimeType,
        }));
        setMessages((prev) =>
          replaceWithAuthoritativeResult(prev, `result-${view.job_id}`, {
            id: nid(),
            kind: "card",
            card: {
              kind: "result",
              id: `result-${view.job_id}`,
              variant: limited ? "limited" : "normal",
              title: limited
                ? copy.research.resultTitleBounded
                : copy.research.resultTitle,
              layers,
              researchId,
              citations,
              officialSources,
              sourceUse,
              materialLimitations,
              attachments,
            },
            handled: true,
          }, `gap-${view.job_id}`)
        );
        return true;
      } catch {
        // A terminal job and its accepted public projection can become visible
        // on adjacent service turns. Retry that bounded handoff before giving up.
      }
    }
    resultLoadedRef.current = false;
    return false;
  }, [researchId]);

  const appendFailedNotice = useCallback((view: JobView) => {
    const jobId = view.job_id;
    const notice = failedNoticeFromJob(view);
    setMessages((prev) => {
      if (prev.some((m) => m.kind === "card" && m.card.id === `failed-${jobId}`)) {
        return prev;
      }
      return [
        ...prev,
        {
          id: nid(),
          kind: "card",
          card: {
            kind: "notice",
            id: `failed-${jobId}`,
            tone: "recoverable",
            title: notice.title,
            body: notice.body,
          },
          handled: false,
        },
      ];
    });
  }, []);

  const appendCancelledNote = useCallback(() => {
    setMessages((prev) => [
      ...prev,
      {
        id: nid(),
        kind: "assistant",
        text: copy.research.cancelledNote,
        streaming: false,
        provenance: "backend_event",
      },
    ]);
  }, []);

  const appendInputFailedNotice = useCallback((inputId: string) => {
    setMessages((prev) => [
      ...prev,
      {
        id: nid(),
        kind: "card",
        card: {
          kind: "notice",
          id: `input-failed-${inputId}`,
          tone: "recoverable",
          title: copy.research.inputFailedTitle,
          body: copy.research.inputFailedBody,
        },
        handled: true,
      },
    ]);
  }, []);

  // —— 补充输入被接受后：对应追问/责任卡标记 handled ——
  const markGapHandled = useCallback((jobId: string, choice?: string) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.kind === "card" && m.card.id === `gap-${jobId}`
          ? { ...m, handled: true, choice: choice ?? m.choice }
          : m,
      ),
    );
  }, []);

  const beginResultRevision = useCallback(() => {
    resultLoadedRef.current = false;
  }, []);

  return {
    messages,
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
  };
}

export type ResearchMessagesApi = ReturnType<typeof useResearchMessages>;
