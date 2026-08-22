/**
 * 合同 → UI 模型映射。
 * 铁律：候选内容不得标为正式事实——claim_ceiling/result.status 原样透传，
 * UI 只按合同字段区分"候选 / 受限 / 失败"，不自行升格。
 */
import type {
  EvidenceCitation,
  InputRequired,
  JobStatus,
  JobView,
  MaterialLimitation,
  OfficialSource,
  ResultView,
  SourceUse,
} from "./types";
import { copy } from "../copy.ts";

/** 研究页相位：驱动"Agent 推进 / 需要你 / 结果 / 失败"四类呈现 */
export type ResearchPhase =
  | "idle" // 尚未创建任务
  | "working" // queued / leased / running / cancelling
  | "need-material" // waiting_for_input + authorization_required=false
  | "need-decision" // waiting_for_input + authorization_required=true
  | "done" // completed，正常候选结果
  | "bounded" // bounded 或结果 claim_ceiling=bounded_missing_data_conclusion
  | "temporarily-unavailable"
  | "invalid-input"
  | "unexpected-failure"
  | "failed" // 兼容没有 typed boundary 的历史任务
  | "cancelled";

export function isTerminal(status: JobStatus): boolean {
  return (
    status === "completed" ||
    status === "bounded" ||
    status === "failed" ||
    status === "cancelled"
  );
}

export function phaseFromJob(job: JobView): ResearchPhase {
  switch (job.status) {
    case "waiting_for_input":
      return job.input_required?.authorization_required
        ? "need-decision"
        : "need-material";
    case "completed":
      return "done";
    case "bounded":
      return "bounded";
    case "failed":
      switch (job.runtime_boundary?.category) {
        case "temporarily_unavailable":
          return "temporarily-unavailable";
        case "invalid_input":
          return "invalid-input";
        case "unexpected_failure":
          return "unexpected-failure";
        default:
          return "failed";
      }
    case "cancelled":
      return "cancelled";
    default:
      return "working";
  }
}

export function failedNoticeFromJob(job: JobView): {
  title: string;
  body: string;
} {
  if (job.runtime_boundary?.failure_code === "tiangong_credential_required") {
    return {
      title: copy.research.tiangongCredentialTitle,
      body: copy.research.tiangongCredentialBody,
    };
  }
  switch (phaseFromJob(job)) {
    case "temporarily-unavailable":
      return {
        title: copy.research.temporarilyUnavailableTitle,
        body: copy.research.temporarilyUnavailableBody,
      };
    case "invalid-input":
      return {
        title: copy.research.invalidInputTitle,
        body: copy.research.invalidInputBody,
      };
    case "unexpected-failure":
      return {
        title: copy.research.unexpectedFailureTitle,
        body: copy.research.unexpectedFailureBody,
      };
    default:
      return {
        title: copy.research.failedTitle,
        body: copy.research.failedBody,
      };
  }
}

export function isBoundedResult(result: ResultView): boolean {
  return (
    result.result?.status === "formal_limited_result_accepted"
    || result.result?.claim_ceiling === "bounded_missing_data_conclusion"
    || result.result?.claim_ceiling === "calculation_structure_only"
  );
}

/** 结果视图 → ResultCard 四层（措辞遵守：候选≠正式事实；受限≠失败） */
export function resultLayers(result: ResultView): {
  limited: boolean;
  layers: { body: string[] }[];
  citations: EvidenceCitation[];
  officialSources: OfficialSource[];
  sourceUse: SourceUse[];
  materialLimitations: MaterialLimitation[];
} {
  const limited = isBoundedResult(result);
  const r = result.result;
  const conclusion = limited
    ? (r?.bounded_conclusion?.trim() || copy.result.missingSummary)
    : (
        r?.summary?.trim()
        || r?.answer?.trim()
        || r?.bounded_conclusion?.trim()
        || copy.result.missingSummary
      );

  const evidence: string[] = [];
  for (const v of r?.values ?? []) {
    evidence.push(`${v.identity}：${v.value} ${v.unit}`);
  }
  evidence.push(
    `结果状态：${r?.status ?? "unknown"}（候选结果——未经专业与权限检查的候选内容不会被写作正式事实）。`,
    `结论强度上限：${r?.claim_ceiling ?? "unknown"}。`,
  );
  if (r?.domain_acceptance?.status) {
    evidence.push(`领域核验：${r.domain_acceptance.status}。`);
  }
  if (r?.official_object_ids?.length) {
    evidence.push(`关联对象：${r.official_object_ids.join("、")}。`);
  }
  for (const source of r?.officialSources ?? []) {
    evidence.push(
      `官方来源：${source.sourceId} · ${source.parseLocation}；发布机构 ${source.sourceAuthority}；版本 ${source.sourceVersion}；访问时间 ${source.accessedAt}；内容哈希 ${source.bodySha256}。`,
    );
  }
  const officialSources = new Map(
    (r?.officialSources ?? []).map((source) => [source.sourceId, source]),
  );
  for (const claim of r?.officialClaimBindings ?? []) {
    const sources = claim.sourceIds.map((sourceId) => {
      const source = officialSources.get(sourceId);
      return source
        ? `${sourceId} · ${source.parseLocation}`
        : sourceId;
    });
    evidence.push(
      `来源支持的主张（${claim.modality}）：${claim.statement} 对应来源：${sources.join("、")}。`,
    );
  }
  if (r?.nativeAuthority) {
    evidence.push(
      `TianGong 原生能力：${r.nativeAuthority.capabilityId}；官方包 ${r.nativeAuthority.sourceIdentity}@${r.nativeAuthority.sourceVersion}；内容哈希 ${r.nativeAuthority.contentSha256}。`,
    );
  }
  if (r?.scientificBoundary) {
    evidence.push(
      `科学边界：LCA 方法：未选择；Provider：未选择；单位：未评价；${r.scientificBoundary.scientificResultCreated ? "已生成科学计算结果" : "未生成科学计算结果"}。`,
    );
  }
  if (r?.route) evidence.push(`当前结果路线：${r.route}。`);
  if (r?.methods?.length) evidence.push(`已用方法：${r.methods.join("、")}。`);
  const completedLabels: Record<string, string> = {
    selected_material_evidence: "已完成必要材料选择",
    accepted_research_result: "已接纳研究结果",
  };
  if (r?.completedStateChanges?.length) {
    evidence.push(
      `已完成状态变化：${r.completedStateChanges.map((item) => completedLabels[item] ?? item).join("、")}。`,
    );
  }

  const limits = [...(r?.limitations ?? [])];
  for (const f of r?.missing_fields ?? []) {
    limits.push(`缺少字段：${f}`);
  }
  for (const limitation of r?.materialLimitations ?? []) {
    if (!limits.includes(limitation.message)) limits.push(limitation.message);
  }
  if (limits.length === 0) {
    limits.push(
      limited
        ? "缺少关键事实材料，结论止步于受限结论。"
        : "本结果当前为候选层级；作为正式科研事实使用前需完成相应检查。",
    );
  }
  if (limited) {
    limits.push("补充材料后可继续推进，结论强度可提升。");
  }

  return {
    limited,
    citations: [...(r?.citations ?? [])],
    officialSources: [...(r?.officialSources ?? [])],
    sourceUse: [...(r?.sourceUse ?? [])],
    materialLimitations: [...(r?.materialLimitations ?? [])],
    layers: [
      {
        body: [
          limited
            ? `${conclusion} 这是边界清楚的受限结论：当前证据不足以下更强的结论。`
            : conclusion,
        ],
      },
      { body: evidence },
      { body: limits },
      {
        body: [
          "研究成果与依据记录在后端项目档案中；已登记的成果附件会列在下方，可直接下载。",
        ],
      },
    ],
  };
}

/** input_required → 追问卡视图 */
export function gapFromInputRequired(req: InputRequired): {
  authorizationRequired: boolean;
  question: string;
  interactionKind: string;
} {
  return {
    authorizationRequired: req.authorization_required === true,
    question: req.question ?? "需要你的补充输入。",
    interactionKind: req.interaction_kind ?? "",
  };
}

/** /projects 状态徽标 */
export const jobStatusBadge: Record<JobStatus, { label: string; tone: "phos" | "amber" | "dim" | "bad" }> = {
  queued: { label: "进行中", tone: "phos" },
  leased: { label: "进行中", tone: "phos" },
  running: { label: "进行中", tone: "phos" },
  waiting_for_input: { label: "等待你", tone: "amber" },
  cancelling: { label: "进行中", tone: "dim" },
  completed: { label: "已完成", tone: "phos" },
  bounded: { label: "受限", tone: "amber" },
  failed: { label: "失败", tone: "bad" },
  cancelled: { label: "已取消", tone: "dim" },
};
