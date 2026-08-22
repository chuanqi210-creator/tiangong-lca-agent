/**
 * D-18 服务合同类型（对齐 openapi-v1.json + fixtures + 实测响应）。
 * 所有视图容忍后端额外字段（解析时只读取已知键，原对象其余键不校验）。
 */

// —— 任务状态机（contracts.py JOB_STATES）——
export type JobStatus =
  | "queued"
  | "leased"
  | "running"
  | "waiting_for_input"
  | "cancelling"
  | "completed"
  | "bounded"
  | "failed"
  | "cancelled";

export interface ContentRef {
  sha256: string;
  role?: string;
}

/** 等待输入时 GET /v1/jobs/{id} 附带的 input_required */
export interface InputRequired {
  interaction_kind?: string;
  authorization_required?: boolean;
  question_contract?: "d18.missing_fact_question/v1";
  question_count?: 1;
  question?: string;
  missing_fields?: string[];
}

export interface RuntimeBoundary {
  category:
    | "temporarily_unavailable"
    | "invalid_input"
    | "unexpected_failure";
  boundary: string;
  failure_code: string;
  retryable: boolean;
  recovery_route: string;
  retry_after_seconds?: number;
}

/** GET /v1/jobs/{id} 视图（列比此处多，宽松读取） */
export interface JobView {
  job_id: string;
  status: JobStatus;
  project_id?: string;
  created_at?: string;
  updated_at?: string;
  cancel_requested?: boolean;
  recovery_count?: number;
  request_ref?: ContentRef | null;
  input_ref?: ContentRef | null;
  input_receipt?: {
    contract: "d18.public_job_input_receipt/v1";
    input_id: string;
    kind: "decision";
    decision_id: string;
    decision: string;
    input_sha256: string;
    receipt_sha256: string;
  } | null;
  state_ref?: ContentRef | null;
  input_required?: InputRequired;
  runtime_boundary?: RuntimeBoundary;
}

/** POST /v1/jobs 响应（202） */
export interface JobCreated {
  job_id: string;
  status: JobStatus;
  request_ref?: ContentRef;
  idempotencyReplayEvidence?: ServiceIdempotencyReplayEvidence;
}

export interface ServiceIdempotencyReplayEvidence {
  contract: "d18.service_idempotency_replay_evidence/v1";
  job_id: string;
  research_id: string;
  request_sha256: string;
  request_ref_sha256: string;
  idempotency_key_sha256: string;
  release_identity: Record<string, string>;
  authentication: {
    contract: "d18.service_idempotency_replay_authentication/v1";
    algorithm: "hmac-sha256";
    evidence_sha256: string;
    tag: string;
  };
}

export interface ResearchRecovery {
  contract: "d18.public_research_recovery/v1";
  research_id: string;
  objective: string;
  user_messages: string[];
  job: JobView;
}

/** GET /v1/jobs/{id}/events 的事件帧 data */
export interface JobEventData {
  event: string;
  sequence: number;
  status?: string | null;
  label: string;
}

// —— 状态投影（chat/completions 的 content JSON 字符串）——
export type ProjectionStatus =
  | "awaiting_user_data"
  | "completed"
  | "failed"
  | string;

export interface StatusProjection {
  contract?: string;
  research_id: string;
  status: ProjectionStatus;
  boundary?: string;
  result_status?: "accepted" | "result_not_yet_accepted" | string;
  result?: ResearchResult | null;
  interaction_kind?: string;
  authorization_required?: boolean;
  question?: string;
  failure_code?: string;
  retryable?: boolean;
  project_record_sha256?: string;
}

// —— 结果视图（GET /v1/jobs/{id}/result）——
export interface ResultValue {
  identity: string;
  unit: string;
  value: number;
}

export interface DomainAcceptance {
  status?: string;
  claim_ceiling?: string;
  may_promote_to_official?: boolean;
  dropped_numeric_claim_count?: number;
  errors?: string[];
}

export interface EvidenceLocator {
  kind: string;
  [key: string]: unknown;
}

export interface EvidenceCitation {
  citationId: string;
  materialId: string;
  revisionId: string;
  displayName: string;
  locator: EvidenceLocator;
  evidenceUrl: string;
  sourceAuthority: string;
  processingStatus: string;
}

export interface OfficialSource {
  sourceId: string;
  authority: "official_public_source";
  sourceAuthority: string;
  requestedUrl: string;
  finalUrl: string;
  accessedAt: string;
  bodySha256: string;
  receiptSha256: string;
  parseLocation: string;
  sourceVersion: string;
  snapshotVersion: string;
}

export interface OfficialClaimBinding {
  claimId: string;
  statement: string;
  modality: string;
  sourceIds: string[];
  sourceSha256s: string[];
}

export interface NativeAuthority {
  provider: "TianGong";
  capabilityId: "tiangong.native.tidas-sdk";
  sourceIdentity: "@tiangong-lca/tidas-sdk";
  sourceVersion: string;
  sourceIntegrity: string;
  contentSha256: string;
}

export interface ScientificBoundary {
  lcaMethod: "not_selected";
  exchangeProvider: "not_selected";
  quantityUnit: "not_evaluated";
  scientificResultCreated: false;
}

export type SourceUseStatus =
  | "used"
  | "available_not_used"
  | "unavailable_partial"
  | "conflict";

export interface SourceUse {
  materialId: string;
  revisionId: string;
  displayName: string;
  status: SourceUseStatus;
  reason: string;
  sourceAuthority: string;
  processingStatus: string;
  selectedLocatorCount: number;
  citedLocatorCount: number;
}

export interface MaterialLimitation {
  code: string;
  message: string;
  blocksStrongerConclusion: boolean;
  materialId?: string;
  revisionId?: string;
}

export interface ResearchResult {
  status: string;
  claim_ceiling: "candidate_only" | "bounded_missing_data_conclusion" | string;
  answer?: string;
  summary?: string;
  bounded_conclusion?: string;
  values?: ResultValue[];
  limitations?: string[];
  missing_fields?: string[];
  domain_acceptance?: DomainAcceptance;
  official_object_ids?: string[];
  source_sha256s?: string[];
  officialSources?: OfficialSource[];
  officialClaimBindings?: OfficialClaimBinding[];
  nativeAuthority?: NativeAuthority | null;
  scientificBoundary?: ScientificBoundary | null;
  citations?: EvidenceCitation[];
  sourceUse?: SourceUse[];
  materialLimitations?: MaterialLimitation[];
  route?: string | null;
  methods?: string[];
  completedStateChanges?: string[];
}

export interface ResultView {
  contract?: string;
  research_id?: string;
  status: "accepted" | "result_not_yet_accepted" | string;
  result: ResearchResult | null;
  project_record_ref?: ContentRef;
  attachments?: D18Attachment[];
  productEvidence?: ServiceProductEvidence | null;
}

export interface ServiceProductEvidence {
  contract: "d18.service_product_evidence/v1";
  job_id: string;
  research_id: string;
  release_manifest: Record<string, unknown>;
  release_identity: Record<string, string>;
  release_roles: Array<{
    role:
      | "backend_service"
      | "accepted_job_request"
      | "worker_execution"
      | "result_projection";
    identity: Record<string, string>;
  }>;
  result_sha256: string;
  execution_receipt_sha256: string;
  project_record: { last_event_sha256: string; event_count: number };
  materials: Array<{
    material_id: string;
    revision_id: string;
    input_sha256: string;
    input_bytes: number;
    processing_receipt_sha256: string;
    qualified_locator_count: number;
  }>;
  official_sources: OfficialSource[];
  authentication: {
    contract: "d18.service_product_evidence_authentication/v1";
    algorithm: "hmac-sha256";
    evidence_sha256: string;
    tag: string;
  };
}

// —— 附件（ArtifactRegistry.register 的公开形状）——
export interface D18Attachment {
  id: string;
  url: string;
  fileUrl: string;
  fileName: string;
  fileType: string;
  mimeType: string;
}

export type JobInputKind = "material" | "decision";
