import type { ComposerSubmission, TransferStatus } from "./material-types.ts";
import { sha256 as nobleSha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";

export const DEFAULT_PART_BYTES = 8 * 1024 * 1024;
export const MULTIPART_THRESHOLD_BYTES = 16 * 1024 * 1024;
const DEFAULT_OBJECTIVE = "检查这些资料中有哪些内容能够支持当前研究，并说明仍缺少什么";

export type SelectionOrigin = "click" | "drop" | "paste" | "directory";

export interface SelectedMaterialFile {
  file: File;
  relativePath: string;
  origin: SelectionOrigin;
}

export interface UploadPartPlan {
  partNumber: number;
  firstByte: number;
  lastByte: number;
}

export interface PersistedUploadTransfer {
  projectId: string;
  uploadId: string;
  fingerprint: string;
  filename: string;
  size: number;
  lastModified: number;
  expectedSha256?: string;
  status: TransferStatus;
  partSize?: number;
}

export interface RuntimeTransfer extends PersistedUploadTransfer {
  file?: File;
  paused: boolean;
  partSha256?: string[];
}

interface CompletedMaterial {
  materialId: string;
  filename: string;
}

interface UploadManagerOptions {
  fetch: typeof globalThis.fetch;
  clock?: () => number;
  concurrency?: number;
}

const allowedTypes = /^(text\/|application\/(pdf|json|xml|zip|vnd\.|octet-stream)|image\/|audio\/)/;

export function validateSelectedFiles(
  files: File[],
  options: {
    origin?: SelectionOrigin;
    maxFiles?: number;
    maxFileBytes?: number;
    batchMaxBytes?: number;
  } = {},
) {
  const maxFiles = options.maxFiles ?? 10;
  const maxFileBytes = options.maxFileBytes ?? 100 * 1024 * 1024;
  const batchMaxBytes = options.batchMaxBytes ?? 500 * 1024 * 1024;
  if (files.length > maxFiles) return { accepted: [] as SelectedMaterialFile[], rejected: [{ code: "batch_count" }] };
  if (files.reduce((sum, file) => sum + file.size, 0) > batchMaxBytes) return { accepted: [] as SelectedMaterialFile[], rejected: [{ code: "batch_size" }] };
  const accepted: SelectedMaterialFile[] = [];
  const rejected: Array<{ code: string; file: File }> = [];
  for (const file of files) {
    if (file.size > maxFileBytes) rejected.push({ code: "file_size", file });
    else if (!allowedTypes.test(file.type || "application/octet-stream")) rejected.push({ code: "type", file });
    else accepted.push({ file, relativePath: (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name, origin: options.origin ?? "click" });
  }
  return { accepted, rejected };
}

export function planUploadParts(totalBytes: number, serverPartSize = DEFAULT_PART_BYTES): UploadPartPlan[] {
  if (totalBytes === 0) return [];
  const partSize = totalBytes > MULTIPART_THRESHOLD_BYTES ? serverPartSize : totalBytes;
  const parts: UploadPartPlan[] = [];
  for (let firstByte = 0, partNumber = 0; firstByte < totalBytes; firstByte += partSize, partNumber += 1) {
    parts.push({ partNumber, firstByte, lastByte: Math.min(totalBytes, firstByte + partSize) - 1 });
  }
  return parts;
}

export function fingerprintFile(file: File) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

async function sha256(blob: Blob) {
  return bytesToHex(nobleSha256(new Uint8Array(await blob.arrayBuffer())));
}

export function createUploadManager(options: UploadManagerOptions) {
  const transfers = new Map<string, RuntimeTransfer>();
  const completed: CompletedMaterial[] = [];
  const concurrency = options.concurrency ?? 3;

  async function schedule(transfer: RuntimeTransfer, already = new Set<number>()) {
    if (!transfer.file) throw new Error("file re-selection is required");
    const plans = planUploadParts(transfer.size, transfer.partSize ?? DEFAULT_PART_BYTES).filter((part) => !already.has(part.partNumber));
    let cursor = 0;
    const workers = Array.from({ length: Math.min(concurrency, plans.length) }, async () => {
      while (!transfer.paused && cursor < plans.length) {
        const part = plans[cursor++];
        if (!part) return;
        const blob = transfer.file!.slice(part.firstByte, part.lastByte + 1);
        const serverPlans = planUploadParts(transfer.size, transfer.partSize ?? DEFAULT_PART_BYTES);
        const workerHashesAlign = serverPlans.length === transfer.partSha256?.length;
        const digest = workerHashesAlign
          ? transfer.partSha256![part.partNumber]!
          : await sha256(blob);
        const response = await options.fetch(`/api/d18/material-uploads/${transfer.uploadId}/parts/${part.partNumber}`, {
          method: "PUT",
          body: blob,
          headers: {
            "Content-Range": `bytes ${part.firstByte}-${part.lastByte}/${transfer.size}`,
            "Content-Type": "application/octet-stream",
            "X-D18-Project-ID": transfer.projectId,
            "X-Part-SHA256": digest,
          },
        });
        if (!response.ok) throw new Error("part upload failed");
      }
    });
    await Promise.all(workers);
  }

  return {
    start(input: { projectId: string; uploadId: string; file: File; partSize: number; partSha256?: string[] }) {
      const transfer: RuntimeTransfer = {
        projectId: input.projectId,
        uploadId: input.uploadId,
        file: input.file,
        fingerprint: fingerprintFile(input.file),
        filename: input.file.name,
        size: input.file.size,
        lastModified: input.file.lastModified,
        status: "uploading",
        paused: false,
        partSize: input.partSize,
        partSha256: input.partSha256,
      };
      transfers.set(input.uploadId, transfer);
      return schedule(transfer);
    },
    pause(uploadId: string) {
      const transfer = transfers.get(uploadId);
      if (transfer) {
        transfer.paused = true;
        transfer.status = "paused";
      }
    },
    async resume(uploadId: string) {
      const transfer = transfers.get(uploadId);
      if (!transfer) throw new Error("upload not found");
      const response = await options.fetch(`/api/d18/material-uploads/${uploadId}`, {
        headers: { "X-D18-Project-ID": transfer.projectId },
      });
      if (!response.ok) throw new Error("upload status failed");
      const state = await response.json() as { upload: { parts?: Array<{ part_number: number }> } };
      transfer.paused = false;
      transfer.status = "uploading";
      await schedule(transfer, new Set((state.upload.parts ?? []).map((part) => part.part_number)));
    },
    async cancel(uploadId: string) {
      const transfer = transfers.get(uploadId);
      if (!transfer) return;
      await options.fetch(`/api/d18/material-uploads/${uploadId}/cancel`, {
        method: "POST",
        headers: { "X-D18-Project-ID": transfer.projectId },
      });
      transfers.delete(uploadId);
    },
    restore(items: PersistedUploadTransfer[]) {
      for (const item of items) transfers.set(item.uploadId, { ...item, paused: true });
    },
    reselect(uploadId: string, file: File, digest: string) {
      const transfer = transfers.get(uploadId);
      if (!transfer) throw new Error("upload not found");
      if (fingerprintFile(file) !== transfer.fingerprint) throw new Error("file fingerprint mismatch");
      if (transfer.expectedSha256 && digest !== transfer.expectedSha256) throw new Error("whole-file SHA mismatch");
      transfer.file = file;
      transfer.expectedSha256 = digest;
      transfer.status = "paused";
      return transfer;
    },
    rememberCompleted(material: CompletedMaterial) { completed.push(material); },
    snapshot() {
      return {
        transfers: [...transfers.values()].map((transfer) => ({
          projectId: transfer.projectId,
          uploadId: transfer.uploadId,
          fingerprint: transfer.fingerprint,
          filename: transfer.filename,
          size: transfer.size,
          lastModified: transfer.lastModified,
          expectedSha256: transfer.expectedSha256,
          status: transfer.status,
          partSize: transfer.partSize,
        })),
        completed: [...completed],
      };
    },
  };
}

export function compileComposerSubmission(text: string, materialIds: string[]): ComposerSubmission & { intent: "research" | "inspect_materials"; startsLcaCalculation: false } {
  return {
    text: text.trim() || DEFAULT_OBJECTIVE,
    materialIds,
    intent: text.trim() ? "research" : "inspect_materials",
    startsLcaCalculation: false,
  };
}
