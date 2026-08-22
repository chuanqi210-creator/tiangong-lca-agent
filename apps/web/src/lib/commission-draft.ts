import {
  getResearchTask,
  type ResearchTaskId,
} from "./research-task-library.ts";

export type CommissionFieldState =
  | "supplied"
  | "agent_may_determine"
  | "unknown"
  | "not_applicable";

export interface CommissionField {
  state: CommissionFieldState;
  value: string;
}

export type ProfessionalFieldKey =
  | "intendedUse"
  | "functionalUnit"
  | "systemBoundary"
  | "geography"
  | "timePeriod"
  | "comparisonBasis"
  | "lciaMethod"
  | "dataPriority"
  | "modellingRules"
  | "uncertainty";

export interface CommissionDraft {
  taskId?: ResearchTaskId;
  objective: string;
  studyObject: string;
  deliverable: string;
  professional: Record<ProfessionalFieldKey, CommissionField>;
}

const labels: Record<ProfessionalFieldKey, string> = {
  intendedUse: "预期用途",
  functionalUnit: "功能单位",
  systemBoundary: "系统边界",
  geography: "地区",
  timePeriod: "时间范围",
  comparisonBasis: "比较基础",
  lciaMethod: "LCIA 方法",
  dataPriority: "数据优先级",
  modellingRules: "建模规则",
  uncertainty: "不确定性要求",
};

function emptyField(): CommissionField {
  return { state: "not_applicable", value: "" };
}

export function createCommissionDraft(
  taskId?: ResearchTaskId,
): CommissionDraft {
  return {
    ...(taskId ? { taskId } : {}),
    objective: taskId ? getResearchTask(taskId).exampleObjective : "",
    studyObject: "",
    deliverable: "",
    professional: {
      intendedUse: emptyField(),
      functionalUnit: emptyField(),
      systemBoundary: emptyField(),
      geography: emptyField(),
      timePeriod: emptyField(),
      comparisonBasis: emptyField(),
      lciaMethod: emptyField(),
      dataPriority: emptyField(),
      modellingRules: emptyField(),
      uncertainty: emptyField(),
    },
  };
}

export function commissionHasObjective(draft: CommissionDraft): boolean {
  return draft.objective.trim().length > 0;
}

function renderField(key: ProfessionalFieldKey, field: CommissionField): string | null {
  switch (field.state) {
    case "not_applicable":
      return null;
    case "agent_may_determine":
      return `${labels[key]}：交由 Agent 根据研究目的判断`;
    case "unknown":
      return `${labels[key]}：目前未知；如其会改变结论，请识别为缺口并说明影响`;
    case "supplied": {
      const value = field.value.trim();
      if (!value) throw new Error(`supplied field is empty: ${key}`);
      return `${labels[key]}：${value}`;
    }
  }
}

export function compileCommissionObjective(draft: CommissionDraft): string {
  const objective = draft.objective.trim();
  if (!objective) throw new Error("research objective is empty");

  const lines = [`研究目标：${objective}`];
  const studyObject = draft.studyObject.trim();
  if (studyObject) lines.push(`研究对象：${studyObject}`);

  for (const key of Object.keys(labels) as ProfessionalFieldKey[]) {
    const line = renderField(key, draft.professional[key]);
    if (line) lines.push(line);
  }

  const deliverable = draft.deliverable.trim();
  if (deliverable) lines.push(`期望交付：${deliverable}`);
  return lines.join("\n");
}
