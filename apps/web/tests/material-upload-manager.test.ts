import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_PART_BYTES,
  compileComposerSubmission,
  createUploadManager,
  fingerprintFile,
  planUploadParts,
  validateSelectedFiles,
} from "../src/lib/d18/upload-manager.ts";
import { uploadMaterialFiles } from "../src/lib/d18/material-client.ts";

function namedFile(name: string, bytes: number, type = "text/plain", lastModified = 1) {
  return new File([new Uint8Array(bytes)], name, { type, lastModified });
}

test("selection accepts clicked, dropped, pasted, and relative-path files", () => {
  const directory = namedFile("inventory.csv", 10, "text/csv") as File & {
    webkitRelativePath: string;
  };
  Object.defineProperty(directory, "webkitRelativePath", { value: "site/a/inventory.csv" });
  for (const origin of ["click", "drop", "paste", "directory"] as const) {
    const result = validateSelectedFiles([directory], { origin });
    assert.equal(result.accepted.length, 1);
    assert.equal(result.accepted[0]?.relativePath, "site/a/inventory.csv");
  }
});

test("selection enforces count, aggregate size, individual size, and type policy", () => {
  assert.equal(validateSelectedFiles(Array.from({ length: 11 }, (_, i) => namedFile(`${i}.txt`, 1))).rejected[0]?.code, "batch_count");
  assert.equal(validateSelectedFiles([namedFile("large.txt", 101)], { maxFileBytes: 100 }).rejected[0]?.code, "file_size");
  assert.equal(validateSelectedFiles([namedFile("a.txt", 60), namedFile("b.txt", 60)], { batchMaxBytes: 100 }).rejected[0]?.code, "batch_size");
  assert.equal(validateSelectedFiles([namedFile("script.exe", 1, "application/x-msdownload")]).rejected[0]?.code, "type");
});

test("production upload client rejects an invalid batch before starting a hash worker", async () => {
  let workerStarted = false;
  const originalWorker = globalThis.Worker;
  Object.assign(globalThis, {
    Worker: class {
      constructor() { workerStarted = true; }
    },
  });
  try {
    await assert.rejects(
      uploadMaterialFiles("p", [namedFile("script.exe", 1, "application/x-msdownload")], "turn"),
      /policy.*type/i,
    );
    assert.equal(workerStarted, false);
  } finally {
    Object.assign(globalThis, { Worker: originalWorker });
  }
});

test("hash worker failure changes production progress from hashing to failed", async () => {
  const originalWorker = globalThis.Worker;
  Object.assign(globalThis, {
    Worker: class {
      onerror: (() => void) | null = null;
      postMessage() { queueMicrotask(() => this.onerror?.()); }
      terminate() {}
    },
  });
  const states: string[] = [];
  try {
    await assert.rejects(
      uploadMaterialFiles("p", [namedFile("inventory.txt", 1)], "turn", (progress) => states.push(progress.status)),
      /hashing failed/i,
    );
    assert.deepEqual(states, ["hashing", "failed"]);
  } finally {
    Object.assign(globalThis, { Worker: originalWorker });
  }
});

test("the production noble worker emits exact whole-file and part digests", async () => {
  let listener: ((event: MessageEvent<unknown>) => void) | undefined;
  const messages: Array<{ type: string; sha256?: string; parts?: Array<{ partNumber: number; sha256: string }> }> = [];
  const priorSelf = globalThis.self;
  Object.assign(globalThis, {
    self: {
      addEventListener: (_type: string, callback: typeof listener) => { listener = callback; },
      postMessage: (message: (typeof messages)[number]) => messages.push(message),
    },
  });
  try {
    await import(`../src/workers/material-hash.worker.ts?test=${Date.now()}`);
    assert.ok(listener);
    listener({ data: { type: "start" } } as MessageEvent<unknown>);
    listener({
      data: {
        type: "chunk",
        partNumber: 0,
        bytes: new TextEncoder().encode("abc").buffer,
      },
    } as MessageEvent<unknown>);
    listener({ data: { type: "finish" } } as MessageEvent<unknown>);
  } finally {
    Object.assign(globalThis, { self: priorSelf });
  }
  assert.deepEqual(messages.at(-1), {
    type: "complete",
    sha256: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    parts: [{
      partNumber: 0,
      sha256: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    }],
  });
});

test("files larger than 16 MiB use 8 MiB parts while smaller files use one part", () => {
  assert.deepEqual(planUploadParts(16 * 1024 * 1024), [{ partNumber: 0, firstByte: 0, lastByte: 16 * 1024 * 1024 - 1 }]);
  assert.deepEqual(planUploadParts(16 * 1024 * 1024 + 1), [
    { partNumber: 0, firstByte: 0, lastByte: DEFAULT_PART_BYTES - 1 },
    { partNumber: 1, firstByte: DEFAULT_PART_BYTES, lastByte: 2 * DEFAULT_PART_BYTES - 1 },
    { partNumber: 2, firstByte: 2 * DEFAULT_PART_BYTES, lastByte: 2 * DEFAULT_PART_BYTES },
  ]);
});

test("a 9 MiB single-part upload computes SHA over the whole server part", async () => {
  const file = namedFile("nine.txt", 9 * 1024 * 1024);
  let observedSha = "";
  const manager = createUploadManager({
    fetch: async (_input, init) => {
      observedSha = new Headers(init?.headers).get("X-Part-SHA256") ?? "";
      return Response.json({ ok: true });
    },
  });
  await manager.start({
    projectId: "p",
    uploadId: "u",
    file,
    partSize: DEFAULT_PART_BYTES,
    partSha256: ["1".repeat(64), "2".repeat(64)],
  });
  assert.notEqual(observedSha, "1".repeat(64));
  assert.equal(observedSha.length, 64);
});

test("pause stops new scheduling and resume asks status then sends only missing parts", async () => {
  const calls: string[] = [];
  let releaseFirst!: () => void;
  const firstPending = new Promise<void>((resolve) => { releaseFirst = resolve; });
  const manager = createUploadManager({
    concurrency: 1,
    fetch: async (input, init) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.endsWith("/parts/0")) await firstPending;
      if ((init?.method ?? "GET") === "GET") return Response.json({ upload: { parts: [{ part_number: 0 }] } });
      return Response.json({ ok: true });
    },
  });
  const upload = manager.start({ projectId: "p", uploadId: "u", file: namedFile("big.txt", 16 * 1024 * 1024 + 1), partSize: DEFAULT_PART_BYTES });
  await Promise.resolve();
  manager.pause("u");
  releaseFirst();
  await upload;
  assert.equal(calls.filter((call) => call.includes("/parts/")).length, 1);
  await manager.resume("u");
  assert.equal(calls.some((call) => call === "GET /api/d18/material-uploads/u"), true);
  assert.equal(calls.some((call) => call.includes("/parts/0") && calls.indexOf(call) > 0), false);
  assert.equal(calls.some((call) => call.includes("/parts/1")), true);
  assert.equal(calls.some((call) => call.includes("/parts/2")), true);
});

test("cancel calls server and removes only transfer state", async () => {
  const calls: string[] = [];
  const manager = createUploadManager({ fetch: async (input, init) => { calls.push(`${init?.method} ${input}`); return Response.json({}); } });
  manager.restore([{ projectId: "p", uploadId: "active", fingerprint: "a", filename: "a", size: 1, lastModified: 1, status: "paused" }]);
  manager.rememberCompleted({ materialId: "material-1", filename: "done.txt" });
  await manager.cancel("active");
  assert.deepEqual(manager.snapshot().completed, [{ materialId: "material-1", filename: "done.txt" }]);
  assert.equal(manager.snapshot().transfers.length, 0);
  assert.equal(calls[0], "POST /api/d18/material-uploads/active/cancel");
});

test("refresh metadata requires matching re-selection when no file handle persists", () => {
  const file = namedFile("same.txt", 4, "text/plain", 123);
  assert.equal(fingerprintFile(file), "same.txt:4:123");
  const manager = createUploadManager({ fetch: async () => Response.json({}) });
  manager.restore([{ projectId: "p", uploadId: "u", fingerprint: fingerprintFile(file), filename: file.name, size: file.size, lastModified: file.lastModified, expectedSha256: "a".repeat(64), status: "reselect_required" }]);
  assert.throws(() => manager.reselect("u", namedFile("wrong.txt", 4, "text/plain", 123), "a".repeat(64)), /fingerprint/);
  assert.throws(() => manager.reselect("u", file, "b".repeat(64)), /SHA/);
  assert.equal(manager.reselect("u", file, "a".repeat(64)).status, "paused");
});

test("reselection resumes through GET status and sends only missing server parts", async () => {
  const file = namedFile("same.txt", 16 * 1024 * 1024 + 1, "text/plain", 123);
  const calls: string[] = [];
  const manager = createUploadManager({
    concurrency: 1,
    fetch: async (input, init) => {
      const method = init?.method ?? "GET";
      calls.push(`${method} ${input}`);
      if (method === "GET") return Response.json({ upload: { parts: [{ part_number: 0 }, { part_number: 2 }] } });
      return Response.json({ ok: true });
    },
  });
  manager.restore([{ projectId: "p", uploadId: "u", fingerprint: fingerprintFile(file), filename: file.name, size: file.size, lastModified: file.lastModified, expectedSha256: "a".repeat(64), status: "reselect_required", partSize: DEFAULT_PART_BYTES }]);
  manager.reselect("u", file, "a".repeat(64));
  await manager.resume("u");
  assert.deepEqual(calls, [
    "GET /api/d18/material-uploads/u",
    "PUT /api/d18/material-uploads/u/parts/1",
  ]);
});

test("attachment-only submission compiles material inspection objective without LCA auto-start", () => {
  assert.deepEqual(compileComposerSubmission("", ["m1", "m2"]), {
    text: "检查这些资料中有哪些内容能够支持当前研究，并说明仍缺少什么",
    materialIds: ["m1", "m2"],
    intent: "inspect_materials",
    startsLcaCalculation: false,
  });
});
