import type { MaterialScope, UploadSessionResponse } from "./material-types.ts";
import { createUploadManager, fingerprintFile, validateSelectedFiles } from "./upload-manager.ts";
import { deleteUploadSession, saveUploadSession } from "./upload-session-store.ts";

export type MaterialProcessingStatus =
  | "pending_security"
  | "queued"
  | "inspecting"
  | "parsing"
  | "ready"
  | "partial"
  | "needs_user"
  | "unsupported"
  | "failed";

export interface MaterialRevisionSummary {
  revision_id: string;
  revision_number: number;
  processing_status: MaterialProcessingStatus | string;
  scientific_status: "candidate_only" | "accepted_project_input" | string;
  created_at: string;
  supersedes_revision_id?: string | null;
  allowed_in_project?: boolean;
  used_in_answer?: boolean;
  input_artifact_id?: string;
}

export interface MaterialNameConflict {
  kind: "same_name_different_content";
  existing_material_ids: string[];
  actions: Array<"replace_as_version" | "keep_separate">;
}

export interface MaterialSummary {
  material_id: string;
  project_id: string;
  display_name: string;
  scope: MaterialScope;
  deleted_at: string | null;
  transfer_status: string;
  type_family: string;
  media_type: string;
  byte_size: number | null;
  tags: string[];
  directory: string;
  source_kind: string;
  current_revision: MaterialRevisionSummary;
  name_conflict?: MaterialNameConflict | null;
}

export interface MaterialDetailResponse {
  material: Omit<MaterialSummary, "current_revision"> & {
    current_revision_id: string;
  };
  current_revision: MaterialRevisionSummary;
  revisions: MaterialRevisionSummary[];
}

export interface MaterialFilters {
  query?: string;
  typeFamily?: string;
  processingStatus?: string;
  scientificStatus?: string;
  scope?: MaterialScope;
  tag?: string;
  directory?: string;
  deleted?: "false" | "true" | "all";
}

export interface MaterialLocatorProjection {
  locator_id: string;
  revision_id: string;
  locator_kind: string;
  locator: Record<string, unknown>;
  scientific_status: string;
}

export interface MaterialPreviewResponse {
  material_id: string;
  revision_id: string;
  processing_status: string;
  scientific_status: string;
  preview: {
    kind: "text" | "spreadsheet" | "metadata_only";
    excerpts?: Array<{ escaped_text: string; source_span: Record<string, unknown> }>;
    rows?: Array<{
      cells: Array<{ displayed_value?: unknown; raw_value?: unknown; formula?: boolean }>;
      source_span?: { kind: string; locator: Record<string, unknown> };
    }>;
    truncated?: boolean;
    thumbnail_available?: boolean;
    available_pages?: number[];
  };
  locators: MaterialLocatorProjection[];
  highlight: MaterialLocatorProjection | null;
}

export function materialListPath(researchId: string, filters: MaterialFilters = {}): string {
  const query = new URLSearchParams();
  const values: Array<[string, string | undefined]> = [
    ["query", filters.query],
    ["type_family", filters.typeFamily],
    ["processing_status", filters.processingStatus],
    ["scientific_status", filters.scientificStatus],
    ["scope", filters.scope],
    ["tag", filters.tag],
    ["directory", filters.directory],
    ["deleted", filters.deleted],
  ];
  for (const [key, value] of values) if (value) query.set(key, value);
  const suffix = query.toString();
  return `/api/d18/research/${encodeURIComponent(researchId)}/materials${suffix ? `?${suffix}` : ""}`;
}

export interface MaterialUploadProgress {
  file: File;
  status: "hashing" | "uploading" | "uploaded" | "failed";
  materialId?: string;
  error?: string;
}

function readMaterialChunk(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    let settled = false;
    const finish = (action: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      action();
    };
    const timeout = setTimeout(() => {
      finish(() => {
        reader.abort();
        reject(new Error("material chunk read timed out"));
      });
    }, 30_000);
    reader.onload = () => {
      finish(() => {
        if (reader.result instanceof ArrayBuffer) resolve(reader.result);
        else reject(new Error("material chunk read did not return bytes"));
      });
    };
    reader.onerror = () => finish(() => reject(reader.error ?? new Error("material chunk read failed")));
    reader.onabort = () => finish(() => reject(new Error("material chunk read was aborted")));
    reader.readAsArrayBuffer(blob);
  });
}

export function hashMaterialFile(file: File): Promise<{ sha256: string; parts: Array<{ partNumber: number; sha256: string }> }> {
  return new Promise((resolve, reject) => {
    const worker = new Worker("/material-hash.worker.js");
    let waiting: ((type: string) => void) | null = null;
    let settled = false;
    const fail = () => {
      if (settled) return;
      settled = true;
      worker.terminate();
      reject(new Error("material hashing failed"));
    };
    const waitFor = (type: string) => new Promise<void>((resume, rejectWait) => {
      const timeout = setTimeout(() => {
        if (waiting === observe) waiting = null;
        rejectWait(new Error(`material hash worker timed out waiting for ${type}`));
      }, 30_000);
      const observe = (observed: string) => {
        if (observed === type) {
          waiting = null;
          clearTimeout(timeout);
          resume();
        }
      };
      waiting = observe;
    });
    worker.onmessage = (event: MessageEvent<{ type: string; sha256?: string; parts?: Array<{ partNumber: number; sha256: string }> }>) => {
      if (event.data.type === "complete" && event.data.sha256 && event.data.parts) {
        settled = true;
        worker.terminate();
        resolve({ sha256: event.data.sha256, parts: event.data.parts });
      } else if (event.data.type === "error") {
        fail();
      } else {
        waiting?.(event.data.type);
      }
    };
    worker.onerror = fail;
    void (async () => {
      let acknowledged = waitFor("ready");
      worker.postMessage({ type: "start" });
      await acknowledged;
      for (let offset = 0, partNumber = 0; offset < file.size; offset += 8 * 1024 * 1024, partNumber += 1) {
        const bytes = await readMaterialChunk(file.slice(offset, offset + 8 * 1024 * 1024));
        acknowledged = waitFor("chunk_complete");
        worker.postMessage({ type: "chunk", partNumber, bytes }, [bytes]);
        await acknowledged;
      }
      worker.postMessage({ type: "finish" });
    })().catch(fail);
  });
}

function notifyMaterialLibraryChanged(researchId: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent("d18:material-library-changed", {
      detail: { researchId },
    }),
  );
}

async function accepted<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(`material request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export async function listProjectMaterials(
  researchId: string,
  filters: MaterialFilters = {},
  fetcher: typeof fetch = globalThis.fetch,
) {
  return accepted<{ materials: MaterialSummary[] }>(
    await fetcher(materialListPath(researchId, filters), { cache: "no-store" }),
  );
}

export async function getProjectMaterial(
  researchId: string,
  materialId: string,
  fetcher: typeof fetch = globalThis.fetch,
) {
  return accepted<MaterialDetailResponse>(
    await fetcher(
      `/api/d18/research/${encodeURIComponent(researchId)}/materials/${encodeURIComponent(materialId)}`,
      { cache: "no-store" },
    ),
  );
}

async function materialMutation<T>(
  url: string,
  idempotencyKey: string,
  options: { method?: string; body?: unknown } = {},
  fetcher: typeof fetch = globalThis.fetch,
) {
  return accepted<T>(
    await fetcher(url, {
      method: options.method ?? "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    }),
  );
}

export function updateProjectMaterialScope(
  researchId: string,
  materialId: string,
  scope: MaterialScope,
  idempotencyKey: string,
  fetcher?: typeof fetch,
) {
  return materialMutation<MaterialDetailResponse>(
    `/api/d18/research/${encodeURIComponent(researchId)}/materials/${encodeURIComponent(materialId)}/scope`,
    idempotencyKey,
    { body: { scope } },
    fetcher,
  );
}

export function addProjectMaterialVersion(
  researchId: string,
  materialId: string,
  sourceMaterialId: string,
  idempotencyKey: string,
  fetcher?: typeof fetch,
) {
  return materialMutation<MaterialDetailResponse>(
    `/api/d18/research/${encodeURIComponent(researchId)}/materials/${encodeURIComponent(materialId)}/versions`,
    idempotencyKey,
    { body: { source_material_id: sourceMaterialId } },
    fetcher,
  );
}

export function withdrawProjectMaterial(
  researchId: string,
  materialId: string,
  idempotencyKey: string,
  fetcher?: typeof fetch,
) {
  return materialMutation<MaterialDetailResponse>(
    `/api/d18/research/${encodeURIComponent(researchId)}/materials/${encodeURIComponent(materialId)}`,
    idempotencyKey,
    { method: "DELETE" },
    fetcher,
  );
}

export function restoreProjectMaterial(
  researchId: string,
  materialId: string,
  idempotencyKey: string,
  fetcher?: typeof fetch,
) {
  return materialMutation<MaterialDetailResponse>(
    `/api/d18/research/${encodeURIComponent(researchId)}/materials/${encodeURIComponent(materialId)}/restore`,
    idempotencyKey,
    undefined,
    fetcher,
  );
}

export async function getProjectMaterialPreview(
  researchId: string,
  materialId: string,
  locatorId?: string,
  fetcher: typeof fetch = globalThis.fetch,
) {
  const query = locatorId ? `?highlight_locator_id=${encodeURIComponent(locatorId)}` : "";
  return accepted<MaterialPreviewResponse>(
    await fetcher(
      `/api/d18/research/${encodeURIComponent(researchId)}/materials/${encodeURIComponent(materialId)}/preview${query}`,
      { cache: "no-store" },
    ),
  );
}

export function reuseProjectOutput(
  researchId: string,
  artifactId: string,
  scope: MaterialScope,
  idempotencyKey: string,
  fetcher?: typeof fetch,
) {
  return materialMutation<{ material: MaterialSummary["material_id"] }>(
    `/api/d18/research/${encodeURIComponent(researchId)}/materials/reuse-output`,
    idempotencyKey,
    { body: { artifact_id: artifactId, scope } },
    fetcher,
  );
}

export async function createMaterialUploads(
  researchId: string,
  files: Array<{ filename: string; declaredMime: string; totalBytes: number; expectedSha256: string }>,
  idempotencyKey: string,
) {
  return accepted<{ uploads: UploadSessionResponse[] }>(
    await fetch(`/api/d18/research/${encodeURIComponent(researchId)}/material-uploads`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({
        uploads: files.map((file) => ({
          filename: file.filename,
          declared_mime: file.declaredMime || "application/octet-stream",
          total_bytes: file.totalBytes,
          expected_sha256: file.expectedSha256,
        })),
      }),
    }),
  );
}

export async function completeMaterialUpload(projectId: string, uploadId: string, scope: MaterialScope) {
  return accepted<{ material: { material_id: string }; revision: { processing_status: "pending_security" } }>(
    await fetch(`/api/d18/material-uploads/${encodeURIComponent(uploadId)}/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-D18-Project-ID": projectId },
      body: JSON.stringify({ scope }),
    }),
  );
}

export async function uploadMaterialFiles(
  researchId: string,
  files: File[],
  scope: MaterialScope,
  onProgress?: (progress: MaterialUploadProgress) => void,
): Promise<string[]> {
  const validation = validateSelectedFiles(files);
  if (validation.rejected.length > 0 || validation.accepted.length !== files.length) {
    const code = validation.rejected[0]?.code ?? "unknown";
    throw new Error(`material upload policy rejected ${code}`);
  }
  const hashed = [] as Array<{ file: File; sha256: string; parts: Array<{ partNumber: number; sha256: string }> }>;
  for (const file of files) {
    onProgress?.({ file, status: "hashing" });
    try {
      const result = await hashMaterialFile(file);
      hashed.push({ file, ...result });
    } catch (error) {
      onProgress?.({ file, status: "failed", error: error instanceof Error ? error.message : "material hashing failed" });
      throw error;
    }
  }
  const created = await createMaterialUploads(
    researchId,
    hashed.map(({ file, sha256 }) => ({
      filename: file.name,
      declaredMime: file.type,
      totalBytes: file.size,
      expectedSha256: sha256,
    })),
    `material-${researchId}-${hashed.map((item) => item.sha256).join("-")}`,
  );
  const manager = createUploadManager({ fetch: (...args) => globalThis.fetch(...args), concurrency: 3 });
  const materialIds: string[] = [];
  for (let index = 0; index < hashed.length; index += 1) {
    const item = hashed[index]!;
    const session = created.uploads[index]!;
    try {
      await saveUploadSession({
        projectId: researchId,
        uploadId: session.upload_id,
        fingerprint: fingerprintFile(item.file),
        filename: item.file.name,
        size: item.file.size,
        lastModified: item.file.lastModified,
        expectedSha256: item.sha256,
        status: "uploading",
        partSize: session.part_size,
      });
      onProgress?.({ file: item.file, status: "uploading" });
      await manager.start({
        projectId: researchId,
        uploadId: session.upload_id,
        file: item.file,
        partSize: session.part_size,
        partSha256: item.parts.map((part) => part.sha256),
      });
      const completed = await completeMaterialUpload(researchId, session.upload_id, scope);
      await deleteUploadSession(session.upload_id);
      materialIds.push(completed.material.material_id);
      onProgress?.({ file: item.file, status: "uploaded", materialId: completed.material.material_id });
      notifyMaterialLibraryChanged(researchId);
    } catch (error) {
      onProgress?.({ file: item.file, status: "failed", error: error instanceof Error ? error.message : "upload failed" });
      throw error;
    }
  }
  return materialIds;
}

export async function resumeMaterialUpload(
  session: import("./upload-manager.ts").PersistedUploadTransfer,
  file: File,
  scope: MaterialScope,
  onProgress?: (progress: MaterialUploadProgress) => void,
): Promise<string> {
  onProgress?.({ file, status: "hashing" });
  try {
    const validation = validateSelectedFiles([file]);
    if (validation.rejected.length > 0) {
      throw new Error(`material upload policy rejected ${validation.rejected[0]?.code}`);
    }
    const hashed = await hashMaterialFile(file);
    const manager = createUploadManager({ fetch: (...args) => globalThis.fetch(...args), concurrency: 3 });
    manager.restore([session]);
    manager.reselect(session.uploadId, file, hashed.sha256);
    onProgress?.({ file, status: "uploading" });
    await manager.resume(session.uploadId);
    const completed = await completeMaterialUpload(session.projectId, session.uploadId, scope);
    await deleteUploadSession(session.uploadId);
    onProgress?.({ file, status: "uploaded", materialId: completed.material.material_id });
    notifyMaterialLibraryChanged(session.projectId);
    return completed.material.material_id;
  } catch (error) {
    onProgress?.({ file, status: "failed", error: error instanceof Error ? error.message : "upload failed" });
    throw error;
  }
}
