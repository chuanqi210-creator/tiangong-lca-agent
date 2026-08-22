/** 对话流的呈现层数据类型（与后端合同无关，仅描述组件渲染所需结构） */

import type {
  EvidenceCitation,
  MaterialLimitation,
  OfficialSource,
  SourceUse,
} from "@/lib/d18/types";

export interface MaterialItem {
  id: string;
  name: string;
  kind: string;
  note?: string;
}

export interface Suggestion {
  id: string;
  label: string;
  intent: "supply" | "decline" | "confirm" | "neutral";
}

export interface GapQuestionCardData {
  kind: "gap_question";
  id: string;
  title: string;
  why: string;
  accept: string;
}

export interface ResponsibilityCardData {
  kind: "responsibility";
  id: string;
  title: string;
  action: string;
  impact: string;
  agreeLabel?: string;
  declineLabel?: string;
}

export interface ResultCardData {
  kind: "result";
  id: string;
  variant: "normal" | "limited";
  title: string;
  /** 固定四层：结论 / 依据 / 限制 / 可带走的成果 */
  layers: { body: string[] }[];
  researchId: string;
  citations: EvidenceCitation[];
  /** 仅来自 Project Record 已核验官方来源投影；用于渲染可直接核对的安全外链。 */
  officialSources?: OfficialSource[];
  sourceUse: SourceUse[];
  materialLimitations: MaterialLimitation[];
  /** 成果层附件（result 视图附带，可经 /api/d18/files/{id} 下载） */
  attachments?: Attachment[];
}

export interface NoticeCardData {
  kind: "notice";
  id: string;
  tone: "recoverable";
  title: string;
  body: string;
}

export type CardData =
  | GapQuestionCardData
  | ResponsibilityCardData
  | ResultCardData
  | NoticeCardData;

export interface Attachment {
  fileUrl: string;
  fileName: string;
  fileType: string;
  mimeType: string;
  fileSize?: number;
}

export type UIMessage =
  | { id: string; kind: "user"; text: string }
  | {
      id: string;
      kind: "assistant";
      text: string;
      streaming: boolean;
      provenance: "backend_event" | "backend_input_receipt" | "local_ui_notice";
    }
  | {
      id: string;
      kind: "narrative";
      items: { text: string; done: boolean }[];
      active: boolean;
    }
  | { id: string; kind: "card"; card: CardData; handled: boolean; choice?: string }
  | { id: string; kind: "attachments"; items: Attachment[] };
