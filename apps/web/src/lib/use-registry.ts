"use client";

/**
 * 注册表的 React 绑定：useSyncExternalStore + 模块级缓存快照，
 * 新建/重命名/删除/状态推进后消费方自动刷新。
 */
import { useSyncExternalStore } from "react";
import {
  listResearch,
  subscribeResearchRegistry,
  type ResearchEntry,
} from "./registry";

const EMPTY: ResearchEntry[] = [];

let snapshot: ResearchEntry[] = [];

function recompute(): void {
  snapshot = listResearch();
}

function subscribe(onStoreChange: () => void): () => void {
  recompute();
  return subscribeResearchRegistry(() => {
    recompute();
    onStoreChange();
  });
}

function getSnapshot(): ResearchEntry[] {
  return snapshot;
}

function getServerSnapshot(): ResearchEntry[] {
  return EMPTY;
}

export function useResearchEntries(): ResearchEntry[] {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function useResearchEntry(researchId: string | null): ResearchEntry | null {
  const entries = useResearchEntries();
  if (!researchId) return null;
  return entries.find((e) => e.researchId === researchId) ?? null;
}
