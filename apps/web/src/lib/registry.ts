/**
 * localStorage 研究项目注册表：支持多项目与刷新恢复。
 * 幂等：idempotencyKey 每个研究固定，重复提交/重试复用同一键；
 * inputSeq 单调递增，同一输入重试复用同一 input_id。
 * 卡片快照独立存储在 CARDS_KEY 下（见文件末尾），与注册表主键互不干扰。
 */
import type { CardData } from "@/components/chat/types";
import type { JobStatus } from "./d18/types";
import type { ResearchRecovery } from "./d18/types";
import type { SourceMaterialInput } from "./d18/materials";
import { sanitizeRegistryForStorage } from "./d18/registry-storage";
import { removeDraft } from "./use-draft";
import { researchIdForNamespace } from "./session-client";

export interface NarrativeRecord {
  sequence: number;
  label: string;
  status?: string | null;
}

export interface ResearchEntry {
  researchId: string;
  title: string;
  /** 展示用研究目标（用户原文） */
  objective: string;
  /** 旧版线协议字段，只为读取历史本地缓存；任何请求不得再使用。 */
  wireObjective?: string;
  /**
   * 旧版本曾把完整材料写入 localStorage。只为读取旧缓存保留类型；
   * read()/write() 都会删除该字段，新的待提交材料只存在页面进程内。
   */
  pendingSourceMaterials?: SourceMaterialInput[];
  /** 仅保存数量，用于刷新后检测材料字节已丢失并 fail closed。 */
  pendingSourceMaterialCount?: number;
  pendingMaterialIds?: string[];
  createdAt: number;
  updatedAt: number;
  jobId: string | null;
  idempotencyKey: string;
  status: JobStatus | null;
  lastEventCursor: number;
  inputSeq: number;
  narrative: NarrativeRecord[];
  userMessages: string[];
  materials: { name: string; note?: string }[];
}

const KEY = "d18.research.registry.v1";

type Registry = Record<string, ResearchEntry>;

const pendingSourceMaterials = new Map<string, SourceMaterialInput[]>();

export function getPendingSourceMaterials(
  researchId: string,
): SourceMaterialInput[] {
  return pendingSourceMaterials.get(researchId) ?? [];
}

export function clearPendingSourceMaterials(researchId: string): void {
  pendingSourceMaterials.delete(researchId);
}

function read(): Registry {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(KEY);
    const registry = raw ? (JSON.parse(raw) as Registry) : {};
    for (const entry of Object.values(registry)) {
      delete entry.pendingSourceMaterials;
    }
    return registry;
  } catch {
    return {};
  }
}

function write(registry: Registry): void {
  try {
    const sanitized = sanitizeRegistryForStorage(registry);
    window.localStorage.setItem(KEY, JSON.stringify(sanitized));
  } catch {
    /* 存储不可用时静默降级 */
  }
  emit();
}

export function listResearch(): ResearchEntry[] {
  return Object.values(read()).sort((a, b) => b.updatedAt - a.updatedAt);
}

export function getResearch(researchId: string): ResearchEntry | null {
  return read()[researchId] ?? null;
}

export function createResearch(
  objective: string,
  opts?: {
    sourceMaterials?: SourceMaterialInput[];
    materials?: { name: string; note?: string }[];
  },
): ResearchEntry {
  const now = Date.now();
  const researchId = `r-${now.toString(36)}`;
  return createResearchWithId(researchId, objective, opts, now);
}

export function createResearchForNamespace(
  projectNamespace: string,
  objective: string,
  opts?: {
    sourceMaterials?: SourceMaterialInput[];
    materials?: { name: string; note?: string }[];
  },
): ResearchEntry {
  const now = Date.now();
  return createResearchWithId(
    researchIdForNamespace(projectNamespace, now),
    objective,
    opts,
    now,
  );
}

function createResearchWithId(
  researchId: string,
  objective: string,
  opts: {
    sourceMaterials?: SourceMaterialInput[];
    materials?: { name: string; note?: string }[];
  } | undefined,
  now: number,
): ResearchEntry {
  const entry: ResearchEntry = {
    researchId,
    title: objective.length > 24 ? `${objective.slice(0, 24)}…` : objective,
    objective,
    pendingSourceMaterialCount: opts?.sourceMaterials?.length ?? 0,
    createdAt: now,
    updatedAt: now,
    jobId: null,
    idempotencyKey: `web-${researchId}`,
    status: null,
    lastEventCursor: 0,
    inputSeq: 0,
    narrative: [],
    userMessages: [],
    materials: opts?.materials ?? [],
  };
  if (opts?.sourceMaterials?.length) {
    pendingSourceMaterials.set(researchId, opts.sourceMaterials);
  }
  const registry = read();
  registry[researchId] = entry;
  write(registry);
  return entry;
}

export function restoreResearchFromAuthority(
  recovery: ResearchRecovery,
): ResearchEntry {
  if (
    recovery.contract !== "d18.public_research_recovery/v1" ||
    !recovery.research_id ||
    recovery.job.project_id !== recovery.research_id ||
    !recovery.job.job_id ||
    !recovery.objective ||
    !Array.isArray(recovery.user_messages) ||
    recovery.user_messages.some((message) => typeof message !== "string")
  ) {
    throw new Error("invalid research recovery response");
  }
  const createdAt = Date.parse(recovery.job.created_at ?? "");
  const updatedAt = Date.parse(recovery.job.updated_at ?? "");
  const now = Date.now();
  const entry: ResearchEntry = {
    researchId: recovery.research_id,
    title:
      recovery.objective.length > 24
        ? `${recovery.objective.slice(0, 24)}…`
        : recovery.objective,
    objective: recovery.objective,
    pendingSourceMaterialCount: 0,
    createdAt: Number.isFinite(createdAt) ? createdAt : now,
    updatedAt: Number.isFinite(updatedAt) ? updatedAt : now,
    jobId: recovery.job.job_id,
    idempotencyKey: `restored-${recovery.job.job_id}`,
    status: recovery.job.status,
    lastEventCursor: 0,
    inputSeq: 0,
    narrative: [],
    userMessages: [...recovery.user_messages],
    materials: [],
  };
  const registry = read();
  registry[entry.researchId] = entry;
  write(registry);
  return entry;
}

export function updateResearch(
  researchId: string,
  patch: Partial<ResearchEntry>,
): ResearchEntry | null {
  const registry = read();
  const entry = registry[researchId];
  if (!entry) return null;
  const next = { ...entry, ...patch, updatedAt: Date.now() };
  registry[researchId] = next;
  write(registry);
  return next;
}

export function removeResearch(researchId: string): void {
  pendingSourceMaterials.delete(researchId);
  const registry = read();
  delete registry[researchId];
  write(registry);
}

/**
 * 重命名研究项目：只改标题，不触碰 updatedAt（避免改名改变列表排序）。
 */
export function renameResearch(researchId: string, title: string): ResearchEntry | null {
  const trimmed = title.trim();
  if (!trimmed) return null;
  const registry = read();
  const entry = registry[researchId];
  if (!entry) return null;
  const next = { ...entry, title: trimmed };
  registry[researchId] = next;
  write(registry);
  return next;
}

/**
 * 轻量订阅机制：同页内任何写操作（create/update/remove/rename）经 write() 触发；
 * 跨标签页通过 storage 事件同步，首个订阅者挂载时接管监听。
 */
const listeners = new Set<() => void>();
let storageListenerAttached = false;

function onStorageEvent(event: StorageEvent): void {
  if (event.key === null || event.key === KEY) emit();
}

function emit(): void {
  for (const listener of listeners) listener();
}

export function subscribeResearchRegistry(listener: () => void): () => void {
  listeners.add(listener);
  if (typeof window !== "undefined" && !storageListenerAttached) {
    window.addEventListener("storage", onStorageEvent);
    storageListenerAttached = true;
  }
  return () => {
    listeners.delete(listener);
    if (
      listeners.size === 0 &&
      storageListenerAttached &&
      typeof window !== "undefined"
    ) {
      window.removeEventListener("storage", onStorageEvent);
      storageListenerAttached = false;
    }
  };
}

/**
 * 卡片快照：结果卡 / 追问卡 / 责任卡 / 提示卡的持久化形态。
 * 独立于主注册表存储（只增不改主键），刷新后先用水合快照渲染，
 * 再由轮询增量校正；快照缺失时终态结果卡可从 /result 重拉兜底。
 */
export interface CardSnapshot {
  card: CardData;
  handled: boolean;
  choice?: string;
}

const CARDS_KEY = "d18.research.cards.v1";

type CardRegistry = Record<string, CardSnapshot[]>;

function readCards(): CardRegistry {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(CARDS_KEY);
    return raw ? (JSON.parse(raw) as CardRegistry) : {};
  } catch {
    return {};
  }
}

function writeCards(registry: CardRegistry): void {
  try {
    window.localStorage.setItem(CARDS_KEY, JSON.stringify(registry));
  } catch {
    /* 存储不可用时静默降级 */
  }
}

export function readCardSnapshots(researchId: string): CardSnapshot[] {
  return readCards()[researchId] ?? [];
}

/** 全量覆写某研究的卡片快照；空数组时清除该研究的快照键。 */
export function writeCardSnapshots(
  researchId: string,
  snapshots: CardSnapshot[],
): void {
  const registry = readCards();
  if (snapshots.length === 0) {
    delete registry[researchId];
  } else {
    registry[researchId] = snapshots;
  }
  writeCards(registry);
}

export function removeCardSnapshots(researchId: string): void {
  const registry = readCards();
  if (!(researchId in registry)) return;
  delete registry[researchId];
  writeCards(registry);
}

/**
 * 删除研究的级联清理：注册表条目 + 卡片快照 + 输入框草稿。
 * removeResearch 本身保持不变；需要完整清理的调用处改用本函数。
 */
export function removeResearchCascade(researchId: string): void {
  removeCardSnapshots(researchId);
  removeDraft(researchId);
  removeResearch(researchId);
}
