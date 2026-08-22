/**
 * 输入框草稿持久化：每 researchId 一个 localStorage key。
 * 500ms 防抖写入，组件 unmount 时 flush，发送成功后清空。
 * SSR 安全：服务端一律返回空串，读写均在 try/catch 中静默降级。
 * key 命名与 registry.ts 的 `d18.research.registry.v1` 同一命名空间。
 */
import { useCallback, useEffect, useRef } from "react";

const KEY_PREFIX = "d18.research.draft.v1:";

function draftStorageKey(researchId: string): string {
  return `${KEY_PREFIX}${researchId}`;
}

/** 非响应式读取：仅在挂载时取一次快照，后续变化不触发重渲染 */
export function readDraft(researchId: string): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(draftStorageKey(researchId)) ?? "";
  } catch {
    return "";
  }
}

function writeDraft(researchId: string, text: string): void {
  if (typeof window === "undefined") return;
  try {
    if (text) {
      window.localStorage.setItem(draftStorageKey(researchId), text);
    } else {
      window.localStorage.removeItem(draftStorageKey(researchId));
    }
  } catch {
    /* 存储不可用时静默降级 */
  }
}

export function removeDraft(researchId: string): void {
  writeDraft(researchId, "");
}

/**
 * 返回防抖的 setDraft 与立即清除的 clearDraft。
 * 卸载时自动 flush 未落盘的最新文本。
 */
export function useDraftActions(researchId: string) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestTextRef = useRef<string | null>(null);

  const flush = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (latestTextRef.current !== null) {
      const text = latestTextRef.current;
      latestTextRef.current = null;
      writeDraft(researchId, text);
    }
  }, [researchId]);

  const setDraft = useCallback(
    (text: string) => {
      latestTextRef.current = text;
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
      }
      timerRef.current = setTimeout(flush, 500);
    },
    [flush],
  );

  const clearDraft = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    latestTextRef.current = null;
    writeDraft(researchId, "");
  }, [researchId]);

  useEffect(() => {
    return () => {
      flush();
    };
  }, [flush]);

  return { setDraft, clearDraft };
}
