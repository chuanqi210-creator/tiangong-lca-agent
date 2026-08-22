/**
 * Fixture 回放引擎：D18_BASE_URL 未配置时，代理层降级到这里。
 * 语义忠实于交接包 fixtures 与实测 python mock：
 * - 状态机沿用合同 JOB_STATES（queued→running→waiting/completed/bounded/failed；cancel/resume）
 * - 事件 label 沿用 events.py 的 _PUBLIC_PROGRESS
 * - 结果视图沿用 mock_server.py 的 read_result 形状（含 claim_ceiling 区分）
 * 引擎为内存态（dev/demo 用途，重启即重置）。
 */
import { createHash, randomUUID } from "crypto";
import { deliverables } from "./demo-files";
import type {
  D18Attachment,
  JobEventData,
  JobStatus,
  JobView,
  ResultView,
  StatusProjection,
} from "./types";

type Scenario = "complete" | "waiting" | "bounded" | "failed";

interface FixtureJob {
  jobId: string;
  researchId: string;
  scenario: Scenario;
  idempotencyKey: string;
  createdAt: number;
  cancelledAt: number | null;
  suppliedAt: number | null;
  resumedAt: number | null;
}

interface EngineState {
  jobs: Map<string, FixtureJob>;
  byIdempotency: Map<string, string>;
}

const g = globalThis as unknown as { __d18Fixture?: EngineState };
function state(): EngineState {
  if (!g.__d18Fixture) {
    g.__d18Fixture = { jobs: new Map(), byIdempotency: new Map() };
    seedFixtures(g.__d18Fixture);
  }
  return g.__d18Fixture;
}

export function scenarioOf(researchId: string): Scenario {
  if (researchId.includes("waiting")) return "waiting";
  if (researchId.includes("bounded")) return "bounded";
  if (researchId.includes("failed")) return "failed";
  return "complete";
}

// —— 时间线（毫秒偏移），语义对应 worker 的 claim/transition ——
const T_CLAIM = 800;
const T_RUNNING = 1400;
const T_FINAL = 2600;
const T_AFTER_INPUT = 1800;

const FINAL_STATUS: Record<Scenario, JobStatus> = {
  complete: "completed",
  waiting: "waiting_for_input",
  bounded: "bounded",
  failed: "failed",
};

const QUESTION = "请补充缺失的事实材料。";

function sha(s: string): string {
  return createHash("sha256").update(s).digest("hex");
}

function refOf(researchId: string): {
  sha256: string;
  role: string;
} {
  const identity = `projects/${researchId}/mock/project-record-projection.json`;
  return { sha256: sha(identity), role: "project_record_projection" };
}

/** 由时间戳推导当前状态与已发生事件（label 与 events.py 一致） */
function project(job: FixtureJob, now: number): {
  status: JobStatus;
  events: JobEventData[];
} {
  const events: JobEventData[] = [];
  const push = (sequence: number, event: string, status: JobStatus, label: string) =>
    events.push({ sequence, event, status, label });

  let seq = 0;
  push(++seq, "created", "queued", "已排队");

  const t0 = job.resumedAt ?? job.createdAt;
  const finalStatus = FINAL_STATUS[job.scenario];
  const waitingCycle = finalStatus === "waiting_for_input";

  // resume 之后重新排队，等待场景会再次回到 waiting_for_input
  const cycleFinal: JobStatus = job.suppliedAt
    ? "completed"
    : waitingCycle
      ? "waiting_for_input"
      : job.resumedAt
        ? finalStatus
        : finalStatus;

  if (now >= t0 + T_CLAIM) push(++seq, "claimed", "running", "已开始");
  if (now >= t0 + T_RUNNING) push(++seq, "transitioned", "running", "正在处理");
  if (job.suppliedAt) {
    push(++seq, "input_supplied", "running", "已收到补充材料");
    if (now >= job.suppliedAt + T_AFTER_INPUT)
      push(++seq, "transitioned", "completed", "状态已更新");
  } else if (now >= t0 + T_FINAL) {
    push(++seq, "transitioned", cycleFinal, "状态已更新");
  }
  if (job.cancelledAt) {
    push(++seq, "cancel_requested", "cancelling", "正在取消");
    push(++seq, "transitioned", "cancelled", "状态已更新");
  }

  let status: JobStatus;
  if (job.cancelledAt) status = "cancelled";
  else if (job.suppliedAt)
    status = now >= job.suppliedAt + T_AFTER_INPUT ? "completed" : "running";
  else if (now < t0 + T_CLAIM) status = "queued";
  else if (now < t0 + T_FINAL) status = "running";
  else status = cycleFinal;

  return { status, events };
}

function toJobView(job: FixtureJob, now: number): JobView {
  const { status } = project(job, now);
  const view: JobView = {
    job_id: job.jobId,
    status,
    project_id: job.researchId,
    created_at: new Date(job.createdAt).toISOString(),
    updated_at: new Date(now).toISOString(),
    cancel_requested: job.cancelledAt !== null,
    recovery_count: 0,
    request_ref: refOf(job.researchId),
    input_ref: null,
    state_ref: refOf(job.researchId),
  };
  if (status === "waiting_for_input") {
    view.input_required = {
      interaction_kind: "factual_data_availability",
      authorization_required: false,
      question: QUESTION,
    };
  }
  return view;
}

// —— 交接包六个 fixture 的预置任务（原 job_id 可直查）——
function seedFixtures(s: EngineState): void {
  const seeds: { jobId: string; researchId: string; scenario: Scenario; ageMs: number }[] = [
    { jobId: "e58f2783-5e0a-58c4-99f5-f79e783e4a13", researchId: "fixture-waiting", scenario: "waiting", ageMs: 60_000 },
    { jobId: "35fb4f92-f912-57a2-b538-3e87b8838236", researchId: "fixture-bounded", scenario: "bounded", ageMs: 60_000 },
    { jobId: "5b4ebdd5-abe0-5b70-a0cb-5a1ce9873b1d", researchId: "fixture-failed", scenario: "failed", ageMs: 60_000 },
  ];
  const now = Date.now();
  for (const seed of seeds) {
    s.jobs.set(seed.jobId, {
      jobId: seed.jobId,
      researchId: seed.researchId,
      scenario: seed.scenario,
      idempotencyKey: `fixture-${seed.researchId}`,
      createdAt: now - seed.ageMs,
      cancelledAt: null,
      suppliedAt: null,
      resumedAt: null,
    });
  }
  // cancelled fixture：静态已取消
  s.jobs.set("45a9dbc9-a399-5a09-b35b-859cc82d2388", {
    jobId: "45a9dbc9-a399-5a09-b35b-859cc82d2388",
    researchId: "fixture-cancelled",
    scenario: "waiting",
    idempotencyKey: "fixture-cancelled",
    createdAt: now - 60_000,
    cancelledAt: now - 30_000,
    suppliedAt: null,
    resumedAt: null,
  });
}

// —— 引擎公开操作（与合同端点一一对应）——

export function fxCreateJob(
  researchId: string,
  idempotencyKey: string,
): { status: number; body: unknown } {
  const s = state();
  const scoped = `${researchId}:${idempotencyKey}`;
  const existingId = s.byIdempotency.get(scoped);
  if (existingId) {
    const existing = s.jobs.get(existingId)!;
    return {
      status: 202,
      body: {
        job_id: existing.jobId,
        status: project(existing, Date.now()).status,
        request_ref: refOf(existing.researchId),
      },
    };
  }
  const job: FixtureJob = {
    jobId: randomUUID(),
    researchId,
    scenario: scenarioOf(researchId),
    idempotencyKey: scoped,
    createdAt: Date.now(),
    cancelledAt: null,
    suppliedAt: null,
    resumedAt: null,
  };
  s.jobs.set(job.jobId, job);
  s.byIdempotency.set(scoped, job.jobId);
  return {
    status: 202,
    body: { job_id: job.jobId, status: "queued", request_ref: refOf(researchId) },
  };
}

export function fxGetJob(jobId: string): { status: number; body: unknown } {
  const job = state().jobs.get(jobId);
  if (!job) return { status: 404, body: { detail: "job not found" } };
  return { status: 200, body: toJobView(job, Date.now()) };
}

export function fxEvents(
  jobId: string,
  after: number,
): { status: number; body?: unknown; frames?: JobEventData[]; cursor?: number } {
  const job = state().jobs.get(jobId);
  if (!job) return { status: 404, body: { detail: "job not found" } };
  const { events } = project(job, Date.now());
  const fresh = events.filter((e) => e.sequence > after);
  const cursor = fresh.length ? fresh[fresh.length - 1].sequence : after;
  return { status: 200, frames: fresh, cursor };
}

export function fxInput(
  jobId: string,
  inputId: string,
  kind: "material" | "decision",
): { status: number; body: unknown } {
  const job = state().jobs.get(jobId);
  if (!job) return { status: 404, body: { detail: "job not found" } };
  const { status } = project(job, Date.now());
  if (status !== "waiting_for_input") {
    return { status: 409, body: { detail: "job is not waiting" } };
  }
  if (!job.suppliedAt) job.suppliedAt = Date.now();
  return {
    status: 202,
    body: { job_id: job.jobId, status: "running", input_ref: refOf(`${job.researchId}-input-${inputId}-${kind}`) },
  };
}

export function fxContinuation(
  parentJobId: string,
  inputId: string,
  text: string,
): { status: number; body: unknown } {
  const s = state();
  const parent = s.jobs.get(parentJobId);
  if (!parent) return { status: 404, body: { detail: "job not found" } };
  const parentStatus = project(parent, Date.now()).status;
  if (
    parentStatus !== "completed" &&
    parentStatus !== "bounded" &&
    parentStatus !== "failed" &&
    parentStatus !== "cancelled"
  ) {
    return {
      status: 409,
      body: { detail: "continuation requires a terminal parent job" },
    };
  }
  const idempotencyKey = `continuation:${parentJobId}:${inputId}:${sha(text)}`;
  const existingId = s.byIdempotency.get(idempotencyKey);
  if (existingId) {
    const existing = s.jobs.get(existingId)!;
    return {
      status: 202,
      body: {
        job_id: existing.jobId,
        status: project(existing, Date.now()).status,
        request_ref: refOf(existing.researchId),
        parent_job_id: parentJobId,
        project_id: parent.researchId,
      },
    };
  }
  const child: FixtureJob = {
    jobId: randomUUID(),
    researchId: parent.researchId,
    scenario: "complete",
    idempotencyKey,
    createdAt: Date.now(),
    cancelledAt: null,
    suppliedAt: null,
    resumedAt: null,
  };
  s.jobs.set(child.jobId, child);
  s.byIdempotency.set(idempotencyKey, child.jobId);
  return {
    status: 202,
    body: {
      job_id: child.jobId,
      status: "queued",
      request_ref: refOf(child.researchId),
      parent_job_id: parentJobId,
      project_id: parent.researchId,
    },
  };
}

export function fxCancel(jobId: string): { status: number; body: unknown } {
  const job = state().jobs.get(jobId);
  if (!job) return { status: 404, body: { detail: "job not found" } };
  const { status } = project(job, Date.now());
  if (status === "completed" || status === "bounded" || status === "failed" || status === "cancelled") {
    return { status: 200, body: toJobView(job, Date.now()) };
  }
  if (!job.cancelledAt) job.cancelledAt = Date.now();
  return { status: 200, body: toJobView(job, Date.now()) };
}

export function fxResume(jobId: string): { status: number; body: unknown } {
  const job = state().jobs.get(jobId);
  if (!job) return { status: 404, body: { detail: "job not found" } };
  const { status } = project(job, Date.now());
  if (status !== "waiting_for_input") {
    return { status: 409, body: { detail: "job is not waiting" } };
  }
  // 合同语义：waiting_for_input → queued（worker 再次认领处理）
  job.resumedAt = Date.now();
  return { status: 200, body: { ...toJobView(job, Date.now()), status: "queued" } };
}

export function fxResult(jobId: string): { status: number; body: unknown } {
  const job = state().jobs.get(jobId);
  if (!job) return { status: 404, body: { detail: "job not found" } };
  const { status } = project(job, Date.now());
  if (status !== "completed" && status !== "bounded") {
    return { status: 409, body: { detail: "result unavailable" } };
  }
  const bounded = job.scenario === "bounded";
  // 附件场景：成果经 ArtifactRegistry 登记后随结果视图下发（演示文件见 demo-files.ts）
  const body: ResultView & { attachments: D18Attachment[] } = {
    contract: "lca_research_kernel.research_result_view/v1",
    research_id: job.researchId,
    status: "accepted",
    result: {
      status: "candidate_only",
      claim_ceiling: bounded
        ? "bounded_missing_data_conclusion"
        : "candidate_only",
      bounded_conclusion: bounded
        ? "受限结果已接纳：缺少关键材料，仅保留受限范围结论。"
        : "候选结果已接纳：2.41 kg CO2-eq（Climate change）。",
      values: bounded
        ? []
        : [{ identity: "Climate change", unit: "kg CO2-eq", value: 2.41 }],
      limitations: bounded ? ["缺少关键事实材料"] : [],
    },
    project_record_ref: refOf(job.researchId),
    attachments: fxAttachments(),
  };
  return { status: 200, body };
}

/** chat/completions 的状态投影（形状对齐实测 mock） */
export function fxProjection(researchId: string): StatusProjection {
  const scenario = scenarioOf(researchId);
  const base = {
    contract: "lca_research_service.accepted_project_record_projection/v1",
    research_id: researchId,
    project_record_sha256: "0".repeat(64),
  };
  if (scenario === "waiting") {
    return {
      ...base,
      status: "awaiting_user_data",
      boundary: "awaiting_user_data",
      interaction_kind: "factual_data_availability",
      authorization_required: false,
      question: QUESTION,
      result_status: "result_not_yet_accepted",
    };
  }
  if (scenario === "failed") {
    return {
      ...base,
      status: "failed",
      boundary: "runtime_failure",
      failure_code: "mock_runtime_failure",
      retryable: false,
      result_status: "result_not_yet_accepted",
    };
  }
  return {
    ...base,
    status: "completed",
    boundary: "result_accepted",
    result_status: "accepted",
    result: {
      status: "candidate_only",
      claim_ceiling:
        scenario === "bounded"
          ? "bounded_missing_data_conclusion"
          : "candidate_only",
      bounded_conclusion:
        scenario === "bounded" ? "受限结果已接纳" : "候选结果已接纳",
    },
  };
}

/** fixture 模式的成果附件（真实后端经 ArtifactRegistry 登记后下发） */
export function fxAttachments() {
  return deliverables.map((d) => {
    const id = `fixture-${d.routeName.replace(/[^a-z0-9]+/gi, "-")}`;
    return {
      id,
      url: `/api/d18/files/${id}`,
      fileUrl: `/api/d18/files/${id}`,
      fileName: d.fileName,
      fileType: d.fileType,
      mimeType: d.mimeType,
    };
  });
}

export function fxFile(artifactId: string):
  | { status: 200; bytes: Uint8Array; mimeType: string; fileName: string }
  | { status: 404 } {
  const name = artifactId.replace(/^fixture-/, "").replace(/-/g, ".");
  const file = deliverables.find(
    (d) => artifactId === `fixture-${d.routeName.replace(/[^a-z0-9]+/gi, "-")}` || name === d.routeName,
  );
  if (!file) return { status: 404 };
  return {
    status: 200,
    bytes: file.build(),
    mimeType: file.mimeType,
    fileName: file.fileName,
  };
}

export function fxProjectRecordRef(researchId: string) {
  return refOf(researchId);
}
