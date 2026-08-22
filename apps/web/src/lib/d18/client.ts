/**
 * 浏览器端 D-18 客户端：只调本站 /api/d18/* 代理，token 不可见。
 */
import type {
  EvidenceCitation,
  JobCreated,
  JobEventData,
  JobInputKind,
  JobView,
  ResearchRecovery,
  ResultView,
} from "./types";
import type { SourceMaterialInput } from "./materials";

const BASE = "/api/d18";

function evidenceIdentity(evidenceUrl: string): {
  researchId: string;
  locatorId: string;
} {
  const match = /^\/v1\/research\/([^/?#]+)\/evidence\/([^/?#]+)$/.exec(
    evidenceUrl,
  );
  if (!match) throw new Error("invalid evidence URL");
  try {
    const researchId = decodeURIComponent(match[1]);
    const locatorId = decodeURIComponent(match[2]);
    if (!researchId || !locatorId) throw new Error("invalid evidence URL");
    return { researchId, locatorId };
  } catch {
    throw new Error("invalid evidence URL");
  }
}

export function citationLocatorId(evidenceUrl: string): string {
  return evidenceIdentity(evidenceUrl).locatorId;
}

export function evidenceBffUrl(evidenceUrl: string): string {
  const identity = evidenceIdentity(evidenceUrl);
  return `${BASE}/research/${encodeURIComponent(identity.researchId)}/evidence/${encodeURIComponent(identity.locatorId)}`;
}

export function getCitationEvidence(
  citation: EvidenceCitation,
): Promise<Record<string, unknown>> {
  return apiJson<Record<string, unknown>>(
    evidenceBffUrl(citation.evidenceUrl).slice(BASE.length),
  );
}

async function apiJson<T>(
  path: string,
  init?: { method?: string; body?: unknown; headers?: Record<string, string> },
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: init?.method ?? "GET",
    headers: {
      ...(init?.body !== undefined
        ? { "Content-Type": "application/json" }
        : {}),
      ...init?.headers,
    },
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* 非 JSON 响应 */
  }
  if (!res.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : `请求失败（${res.status}）`;
    const err = new Error(detail) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return data as T;
}

export function createJob(
  researchId: string,
  objective: string,
  idempotencyKey: string,
  sourceMaterials: SourceMaterialInput[] = [],
  materialIds: string[] = [],
): Promise<JobCreated> {
  return apiJson<JobCreated>("/jobs", {
    method: "POST",
    body: {
      research_id: researchId,
      messages: [{ role: "user", content: objective }],
      source_materials: sourceMaterials,
      material_ids: materialIds,
    },
    headers: { "Idempotency-Key": idempotencyKey },
  });
}

export function getJob(jobId: string): Promise<JobView> {
  return apiJson<JobView>(`/jobs/${encodeURIComponent(jobId)}`);
}

export function recoverResearch(researchId: string): Promise<ResearchRecovery> {
  return apiJson<ResearchRecovery>(
    `/research/${encodeURIComponent(researchId)}/jobs/latest`,
  );
}

/** 事件端点是有界转储（heartbeat + [DONE] 结束），读完即返回增量 */
export async function fetchEvents(
  jobId: string,
  after: number,
): Promise<{ events: JobEventData[]; cursor: number }> {
  const res = await fetch(
    `${BASE}/jobs/${encodeURIComponent(jobId)}/events`,
    { headers: { "Last-Event-ID": String(after) } },
  );
  if (!res.ok || !res.body) {
    throw new Error(`事件流请求失败（${res.status}）`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const events: JobEventData[] = [];
  let cursor = after;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const dataLine = frame
        .split("\n")
        .find((l) => l.startsWith("data:"))
        ?.slice(5)
        .trim();
      if (!dataLine || dataLine === "[DONE]") continue;
      try {
        const data = JSON.parse(dataLine);
        if (typeof data.label === "string" && typeof data.sequence === "number") {
          events.push(data as JobEventData);
          cursor = Math.max(cursor, data.sequence);
        } else if (typeof data.cursor === "number") {
          cursor = Math.max(cursor, data.cursor);
        }
      } catch {
        /* 忽略无法解析的帧 */
      }
    }
  }
  return { events, cursor };
}

export function supplyInput(
  jobId: string,
  inputId: string,
  kind: JobInputKind,
  payload: Record<string, unknown>,
): Promise<{ job_id: string; status: string }> {
  return apiJson(`/jobs/${encodeURIComponent(jobId)}/inputs`, {
    method: "POST",
    body: { input_id: inputId, kind, payload },
  });
}

export function createContinuation(
  jobId: string,
  inputId: string,
  text: string,
): Promise<JobCreated & { parent_job_id: string; project_id: string }> {
  return apiJson(`/jobs/${encodeURIComponent(jobId)}/continuations`, {
    method: "POST",
    body: { input_id: inputId, text },
  });
}

export function cancelJob(jobId: string): Promise<JobView> {
  return apiJson<JobView>(`/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
  });
}

export function resumeJob(jobId: string): Promise<JobView> {
  return apiJson<JobView>(`/jobs/${encodeURIComponent(jobId)}/resume`, {
    method: "POST",
  });
}

export function getResult(jobId: string): Promise<ResultView> {
  return apiJson<ResultView>(`/jobs/${encodeURIComponent(jobId)}/result`);
}
