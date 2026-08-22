export type MaterialScope = "turn" | "project";
export type TransferStatus =
  | "selected"
  | "hashing"
  | "uploading"
  | "paused"
  | "uploaded"
  | "failed"
  | "cancelled"
  | "reselect_required";

export interface ComposerSubmission {
  text: string;
  materialIds: string[];
}

export interface AttachmentSubmissionState {
  status: TransferStatus;
  materialId?: string;
  omitted: boolean;
}

export function attachmentSubmissionDecision(items: AttachmentSubmissionState[]) {
  const blocked = items.some(
    (item) => (item.status === "failed" || item.status === "reselect_required") && !item.omitted,
  );
  return blocked
    ? {
        ready: false as const,
        materialIds: [] as string[],
        blockedActions: ["retry", "remove", "omit"] as const,
      }
    : {
        ready: true as const,
        materialIds: items
          .filter((item) => item.status === "uploaded" && !item.omitted)
          .flatMap((item) => (item.materialId ? [item.materialId] : [])),
        blockedActions: [] as const,
      };
}

export function canSubmitComposer(text: string, items: AttachmentSubmissionState[]) {
  const decision = attachmentSubmissionDecision(items);
  return decision.ready && (text.trim().length > 0 || decision.materialIds.length > 0 || items.some((item) => item.omitted));
}

export interface UploadSessionResponse {
  upload_id: string;
  project_id: string;
  filename: string;
  total_bytes: number;
  part_size: number;
  transfer_status: string;
  expires_at: string;
  parts: Array<{ part_number: number; sha256: string }>;
}
