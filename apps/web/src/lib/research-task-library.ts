export type ResearchOutcomeKind =
  | "goal_and_scope"
  | "official_data"
  | "inventory_gap"
  | "calculation"
  | "comparison_uncertainty"
  | "interpretation"
  | "review"
  | "enterprise_decision"
  | "learning";

export type ResearchAudience = "professional" | "enterprise" | "learner";

export interface ResearchTaskDescriptor {
  id: string;
  outcomeKind: ResearchOutcomeKind;
  title: string;
  summary: string;
  audiences: readonly ResearchAudience[];
  useWhen: string;
  minimumInput: readonly string[];
  agentWork: readonly string[];
  deliverables: readonly string[];
  boundary: string;
  exampleObjective: string;
}

export const RESEARCH_TASKS = [
  {
    id: "goal-and-scope",
    outcomeKind: "goal_and_scope",
    title: "目标与范围设计",
    summary: "把决策问题转成可执行的功能单位、系统边界和数据计划。",
    audiences: ["professional", "enterprise", "learner"],
    useWhen: "研究目的明确，但功能单位、比较基础或边界尚未冻结。",
    minimumInput: ["研究对象", "预期用途或需要支持的决定"],
    agentWork: ["澄清目标与范围", "识别关键假设和数据需求"],
    deliverables: ["目标与范围说明", "功能单位与边界草案", "数据需求清单"],
    boundary: "未获得用户独占事实时会保留假设或形成待补充项。",
    exampleObjective:
      "为一款镀锌钢制检修口设计摇篮到大门 LCA 的功能单位、系统边界和数据计划。",
  },
  {
    id: "official-data",
    outcomeKind: "official_data",
    title: "TianGong 数据查找与核验",
    summary: "定位精确版本的数据对象，并说明来源、单位和适用范围。",
    audiences: ["professional", "enterprise"],
    useWhen: "需要为研究选择官方数据，或核验已有数据是否适用。",
    minimumInput: ["目标材料或过程", "地区、时间或技术要求"],
    agentWork: ["检索官方数据", "核验身份、版本、单位和适用范围"],
    deliverables: ["版本化数据表", "选择依据", "缺失或冲突清单"],
    boundary:
      "找不到满足条件的数据时会保留缺口，不会以相似名称冒充精确对象。",
    exampleObjective:
      "查找 TianGong 中适用于华东地区的热轧钢板数据，核验精确版本、参考流和时间代表性。",
  },
  {
    id: "inventory-gap",
    outcomeKind: "inventory_gap",
    title: "清单整理与数据缺口",
    summary: "把 BOM 或活动数据分成正式数据、代理、假设和关键缺口。",
    audiences: ["professional", "enterprise"],
    useWhen: "已有物料或活动数据，但尚不清楚哪些信息足以建模。",
    minimumInput: ["BOM、活动数据或供应商材料", "研究对象和预期边界"],
    agentWork: ["分类数据身份", "检查单位与完整性", "确定关键缺口"],
    deliverables: ["数据分类表", "代理与假设账本", "补充数据优先级"],
    boundary:
      "用户独占数据缺失时只形成透明代理或受限结论，不补造原始值。",
    exampleObjective:
      "审查这份产品 BOM，区分正式数据、供应商数据、透明代理、假设和会阻断结论的缺口。",
  },
  {
    id: "lcia-calculation",
    outcomeKind: "calculation",
    title: "LCI / LCIA 模型与计算",
    summary: "从经过核验的清单和方法形成可重放的模型与计算结果。",
    audiences: ["professional"],
    useWhen: "功能单位、边界和输入已经足够明确，需要执行确定性计算。",
    minimumInput: ["功能单位与系统边界", "清单或已登记数据", "方法要求"],
    agentWork: ["校验输入和单位", "构建或调用模型", "记录方法与计算收据"],
    deliverables: ["LCI/LCIA 结果", "计算依据", "机器可读工件"],
    boundary:
      "输入或 Provider 闭包不足时不会把不完整运行包装成正式计算结果。",
    exampleObjective:
      "基于已核验清单，使用指定 LCIA 方法计算结果，并保留输入、单位、方法版本和重放证据。",
  },
  {
    id: "comparison-uncertainty",
    outcomeKind: "comparison_uncertainty",
    title: "方案比较与不确定性",
    summary: "在共享比较基础上评估差异、敏感性和结论翻转条件。",
    audiences: ["professional", "enterprise"],
    useWhen: "需要比较方案，且必须判断结论是否受关键假设影响。",
    minimumInput: ["可比的两个或多个方案", "共享功能单位和边界"],
    agentWork: ["核验可比性", "执行敏感性或情景分析", "识别翻转条件"],
    deliverables: ["比较表", "敏感性结果", "稳健结论与翻转条件"],
    boundary:
      "比较基础不一致时先修正或拒绝比较，不以归一化措辞掩盖不可比性。",
    exampleObjective:
      "比较两个包装方案的环境表现，并识别重复使用次数或回收率在什么条件下会翻转结论。",
  },
  {
    id: "result-interpretation",
    outcomeKind: "interpretation",
    title: "结果解释与热点分析",
    summary: "解释多指标结果、贡献来源和可以支持的结论强度。",
    audiences: ["professional", "enterprise", "learner"],
    useWhen: "已有 LCIA 结果，需要形成科学解释或决策摘要。",
    minimumInput: ["结果值、单位和方法身份", "研究目的和边界"],
    agentWork: ["核对方法与单位", "解释贡献和限制", "约束主张强度"],
    deliverables: ["专业解释", "决策摘要", "限制与后续分析建议"],
    boundary:
      "不会跨不兼容单位直接排序，也不会把零值解释成没有排放或没有影响。",
    exampleObjective:
      "解释这组多指标 LCIA 结果，按单位和方法分组，并说明能够支持和不能支持的结论。",
  },
  {
    id: "model-report-review",
    outcomeKind: "review",
    title: "模型、报告与供应商声明审查",
    summary: "检查第三方材料的范围、数据、方法、证据和结论是否一致。",
    audiences: ["professional", "enterprise"],
    useWhen: "需要判断现有模型、报告或碳足迹声明能否支持预期用途。",
    minimumInput: ["待审查工件", "预期使用场景或决策"],
    agentWork: ["核验边界和数据身份", "查找矛盾与缺口", "评估主张强度"],
    deliverables: ["问题清单", "可接受内容", "补证或修正建议"],
    boundary:
      "没有底层证据时不会仅凭报告排版或结论文字判断其科学有效。",
    exampleObjective:
      "审查供应商提供的产品碳足迹报告，判断其是否足以支持采购比较并列出需要补证的内容。",
  },
  {
    id: "enterprise-decision",
    outcomeKind: "enterprise_decision",
    title: "企业合规与决策支持",
    summary: "把法规、供应链数据和 LCA 证据转成边界明确的行动建议。",
    audiences: ["enterprise", "professional"],
    useWhen: "企业需要核验成本、合规主张、采购或减排决策。",
    minimumInput: ["产品与交易背景", "客户主张或需要支持的决定"],
    agentWork: ["核验适用规则和数值", "区分法定义务与建议", "形成准备清单"],
    deliverables: ["直接判断", "计算或证据依据", "行动与材料清单"],
    boundary:
      "法规时点或关键业务事实不明时会收窄结论，不替代正式法律意见。",
    exampleObjective:
      "判断客户给出的 CBAM 成本估算是否合理，并列出企业应准备的数据、文件和谈判依据。",
  },
  {
    id: "guided-learning",
    outcomeKind: "learning",
    title: "LCA 概念解释与学习",
    summary: "用同一组事实解释专业概念、方法选择和结果差异。",
    audiences: ["learner", "enterprise", "professional"],
    useWhen: "需要理解概念，或为团队建立共同的 LCA 判断基础。",
    minimumInput: ["希望理解的问题", "可选的产品或数据示例"],
    agentWork: ["解释术语与因果关系", "给出边界清楚的例子", "指出常见误区"],
    deliverables: ["分层解释", "示例", "自检问题或学习路径"],
    boundary: "教学示例不会被登记为真实项目的科学结果。",
    exampleObjective:
      "解释功能单位、分配和系统边界如何改变 LCA 结果，并给出一个可核对的简单示例。",
  },
] as const satisfies readonly ResearchTaskDescriptor[];

export type ResearchTaskId = (typeof RESEARCH_TASKS)[number]["id"];

export interface ResearchTaskSection {
  id: "professional" | "learning";
  tasks: readonly ResearchTaskDescriptor[];
}

export function taskLibrarySections(): readonly ResearchTaskSection[] {
  return [
    {
      id: "professional",
      tasks: RESEARCH_TASKS.filter((task) => task.outcomeKind !== "learning"),
    },
    {
      id: "learning",
      tasks: RESEARCH_TASKS.filter((task) => task.outcomeKind === "learning"),
    },
  ];
}

export function isResearchTaskId(value: string): value is ResearchTaskId {
  return RESEARCH_TASKS.some((task) => task.id === value);
}

export function getResearchTask(id: string): ResearchTaskDescriptor {
  const task = RESEARCH_TASKS.find((candidate) => candidate.id === id);
  if (!task) throw new Error(`unknown research task: ${id}`);
  return task;
}
