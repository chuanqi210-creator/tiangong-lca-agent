import assert from "node:assert/strict";
import test from "node:test";

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { Window } from "happy-dom";
import React from "react";

import {
  PromptInput,
  PromptInputProvider,
  PromptInputTextarea,
  usePromptInputAttachments,
} from "../src/components/ai-elements/prompt-input.tsx";
import { Composer } from "../src/components/chat/Composer.tsx";
import { AttachmentTray } from "../src/components/materials/AttachmentTray.tsx";
import { ResponsibilityCard } from "../src/components/chat/ResponsibilityCard.tsx";
import { MaterialPanel } from "../src/components/materials/MaterialPanel.tsx";
import { MaterialPreview } from "../src/components/materials/MaterialPreview.tsx";
import { HeroInput } from "../src/app/page.tsx";

const window = new Window({ url: "http://localhost" });
Object.assign(globalThis, {
  document: window.document,
  HTMLElement: window.HTMLElement,
  HTMLInputElement: window.HTMLInputElement,
  localStorage: window.localStorage,
  URL: window.URL,
  window,
});
Object.defineProperty(globalThis, "navigator", { configurable: true, value: window.navigator });
Object.assign(globalThis.URL, {
  createObjectURL: () => "blob:test",
  revokeObjectURL: () => {},
});

function memoryIndexedDb(rows: unknown[]) {
  return {
    open() {
      const request: Record<string, unknown> = {};
      queueMicrotask(() => {
        const db = {
          close() {},
          createObjectStore() {},
          transaction() {
            const transaction: Record<string, unknown> = {
              objectStore() {
                return {
                  getAll() {
                    const getRequest: Record<string, unknown> = {};
                    queueMicrotask(() => {
                      Object.assign(getRequest, { result: rows });
                      (getRequest.onsuccess as (() => void) | undefined)?.();
                    });
                    return getRequest;
                  },
                  delete() {
                    queueMicrotask(() => (transaction.oncomplete as (() => void) | undefined)?.());
                  },
                  put() {
                    queueMicrotask(() => (transaction.oncomplete as (() => void) | undefined)?.());
                  },
                };
              },
            };
            return transaction;
          },
        };
        Object.assign(request, { result: db });
        (request.onsuccess as (() => void) | undefined)?.();
      });
      return request;
    },
  } as unknown as IDBFactory;
}

function CaptureAttachments({ onReady }: { onReady: (value: ReturnType<typeof usePromptInputAttachments>) => void }) {
  const attachments = usePromptInputAttachments();
  onReady(attachments);
  return null;
}

test.afterEach(() => cleanup());

test("material recovery responsibility card names retry and this-run omission explicitly", () => {
  const choices: string[] = [];
  const mounted = render(
    <ResponsibilityCard
      card={{
        kind: "responsibility",
        id: "recover-material",
        title: "资料处理需要你的决定",
        action: "选择下一步",
        impact: "决定会进入研究档案",
        agreeLabel: "重新处理失败资料",
        declineLabel: "本轮不使用失败资料",
      }}
      handled={false}
      onChoice={(choice) => choices.push(choice)}
    />,
  );
  fireEvent.click(mounted.getByRole("button", { name: "重新处理失败资料" }));
  fireEvent.click(mounted.getByRole("button", { name: "本轮不使用失败资料" }));
  assert.deepEqual(choices, ["同意", "暂不"]);
  mounted.unmount();
});

test("research Composer mounts with its PromptInput provider", async () => {
  Object.assign(globalThis, { indexedDB: memoryIndexedDb([]) });
  const mounted = render(
    <Composer disabled={false} draftId="project-a" onSend={() => {}} placeholder="objective" />,
  );
  await mounted.findByPlaceholderText("objective");
  mounted.unmount();
});

test("real PromptInput rejects aggregate policy before adding files and exposes error", async () => {
  let attachments!: ReturnType<typeof usePromptInputAttachments>;
  let error = "";
  const mounted = render(
    <PromptInputProvider>
      <PromptInput batchMaxBytes={100} onError={(value) => { error = value.message; }} onSubmit={() => {}}>
        <CaptureAttachments onReady={(value) => { attachments = value; }} />
        <PromptInputTextarea />
      </PromptInput>
    </PromptInputProvider>,
  );
  const input = mounted.getByLabelText("Upload files");
  fireEvent.change(input, {
    target: {
      files: [
        new File([new Uint8Array(60)], "a.txt", { type: "text/plain" }),
        new File([new Uint8Array(60)], "b.txt", { type: "text/plain" }),
      ],
    },
  });
  await waitFor(() => assert.equal(attachments.files.length, 0));
  assert.match(error, /batch/i);
  mounted.unmount();
});

test("real PromptInput rejects the whole batch when one selected file violates policy", async () => {
  let attachments!: ReturnType<typeof usePromptInputAttachments>;
  let error = "";
  const mounted = render(
    <PromptInputProvider>
      <PromptInput accept="text/*" maxFiles={2} onError={(value) => { error = value.code; }} onSubmit={() => {}}>
        <CaptureAttachments onReady={(value) => { attachments = value; }} />
        <PromptInputTextarea />
      </PromptInput>
    </PromptInputProvider>,
  );
  fireEvent.change(mounted.getByLabelText("Upload files"), {
    target: { files: [
      new File(["ok"], "ok.txt", { type: "text/plain" }),
      new File(["bad"], "bad.exe", { type: "application/x-msdownload" }),
    ] },
  });
  await waitFor(() => assert.equal(error, "accept"));
  assert.equal(attachments.files.length, 0);
  mounted.unmount();
});

test("production Composer rejects an unsupported file before starting upload and shows the policy error", async () => {
  Object.assign(globalThis, { indexedDB: memoryIndexedDb([]) });
  const fetchCalls: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    fetchCalls.push(String(input));
    return Response.json({});
  };
  try {
    const mounted = render(
      <Composer disabled={false} draftId="project-policy" onSend={() => {}} placeholder="objective-policy" />,
    );
    await mounted.findByPlaceholderText("objective-policy");
    fireEvent.change(mounted.getByLabelText("Upload files"), {
      target: {
        files: [new File(["binary"], "blocked.exe", { type: "application/x-msdownload" })],
      },
    });
    await mounted.findByRole("alert");
    assert.match(mounted.getByRole("alert").textContent ?? "", /支持|类型/);
    assert.equal(fetchCalls.length, 0);
    mounted.unmount();
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("real folder input retains relative paths in the existing attachment store", async () => {
  let attachments!: ReturnType<typeof usePromptInputAttachments>;
  const mounted = render(
    <PromptInputProvider>
      <PromptInput onSubmit={() => {}}>
        <CaptureAttachments onReady={(value) => { attachments = value; }} />
        <PromptInputTextarea />
      </PromptInput>
    </PromptInputProvider>,
  );
  const folderInput = mounted.getByLabelText("Upload folder");
  assert.equal(folderInput.hasAttribute("webkitdirectory"), true);
  const file = new File(["inventory"], "inventory.csv", { type: "text/csv" }) as File & { webkitRelativePath: string };
  Object.defineProperty(file, "webkitRelativePath", { value: "site/a/inventory.csv" });
  fireEvent.change(folderInput, { target: { files: [file] } });
  await waitFor(() => assert.equal(attachments.files[0]?.relativePath, "site/a/inventory.csv"));
  mounted.unmount();
});

test("real drop path recursively traverses directory entries into the existing attachment store", async () => {
  let attachments!: ReturnType<typeof usePromptInputAttachments>;
  const mounted = render(
    <PromptInputProvider>
      <PromptInput onSubmit={() => {}}>
        <CaptureAttachments onReady={(value) => { attachments = value; }} />
        <PromptInputTextarea />
      </PromptInput>
    </PromptInputProvider>,
  );
  const nested = new File(["nested"], "inventory.csv", { type: "text/csv" });
  let read = false;
  const fileEntry = {
    isFile: true,
    isDirectory: false,
    fullPath: "/site/a/inventory.csv",
    file(callback: (file: File) => void) { callback(nested); },
  };
  const directoryEntry = {
    isFile: false,
    isDirectory: true,
    fullPath: "/site",
    createReader() {
      return {
        readEntries(callback: (entries: unknown[]) => void) {
          callback(read ? [] : [fileEntry]);
          read = true;
        },
      };
    },
  };
  fireEvent.drop(mounted.container.querySelector("form")!, {
    dataTransfer: {
      files: [],
      items: [{ kind: "file", webkitGetAsEntry: () => directoryEntry }],
      types: ["Files"],
    },
  });
  await waitFor(() => assert.equal(attachments.files[0]?.relativePath, "site/a/inventory.csv"));
  mounted.unmount();
});

test("ordinary drop falls back when DataTransferItem has no WebKit entry API", async () => {
  let attachments!: ReturnType<typeof usePromptInputAttachments>;
  const mounted = render(
    <PromptInputProvider>
      <PromptInput onSubmit={() => {}}>
        <CaptureAttachments onReady={(value) => { attachments = value; }} />
        <PromptInputTextarea />
      </PromptInput>
    </PromptInputProvider>,
  );
  const file = new File(["ordinary"], "ordinary.txt", { type: "text/plain" });
  fireEvent.drop(mounted.container.querySelector("form")!, {
    dataTransfer: {
      files: [file],
      items: [{ kind: "file", getAsFile: () => file }],
      types: ["Files"],
    },
  });
  await waitFor(() => assert.equal(attachments.files[0]?.filename, "ordinary.txt"));
  mounted.unmount();
});

test("real paste adds a clipboard file through the PromptInput attachment store", async () => {
  let attachments!: ReturnType<typeof usePromptInputAttachments>;
  const mounted = render(
    <PromptInputProvider>
      <PromptInput onSubmit={() => {}}>
        <CaptureAttachments onReady={(value) => { attachments = value; }} />
        <PromptInputTextarea placeholder="paste-here" />
      </PromptInput>
    </PromptInputProvider>,
  );
  const pasted = new File(["pasted"], "pasted.txt", { type: "text/plain" });
  fireEvent.paste(mounted.getByPlaceholderText("paste-here"), {
    clipboardData: { items: [{ kind: "file", getAsFile: () => pasted }] },
  });
  await waitFor(() => assert.equal(attachments.files[0]?.filename, "pasted.txt"));
  mounted.unmount();
});

test("landing HeroInput keeps real project scope state and submits it", async () => {
  const submissions: Array<{ text: string; scope: string }> = [];
  const mounted = render(
    <HeroInput creating={false} onSubmit={async (text, _files, scope) => { submissions.push({ text, scope }); }} />,
  );
  assert.match(
    mounted.getByRole("button", { name: /资料范围.*整个研究项目/ }).textContent ?? "",
    /整个研究项目/,
  );
  fireEvent.change(mounted.getByLabelText("Upload files"), {
    target: { files: [new File(["inventory"], "inventory.txt", { type: "text/plain" })] },
  });
  fireEvent.submit(mounted.container.querySelector("form")!);
  await waitFor(() => assert.deepEqual(submissions, [{ text: "", scope: "project" }]));
  mounted.unmount();
});

const summary = (id: string, directory: string, conflict: string[] = []) => ({
  material_id: id,
  project_id: "project-a",
  display_name: "inventory.csv",
  scope: "project" as const,
  deleted_at: null,
  transfer_status: "completed",
  type_family: "table",
  media_type: "text/csv",
  byte_size: 12,
  tags: ["supplier"],
  directory,
  source_kind: "local_upload",
  current_revision: {
    revision_id: `revision-${id}`,
    revision_number: 1,
    processing_status: "ready",
    scientific_status: "candidate_only",
    created_at: "2026-08-13T00:00:00Z",
  },
  name_conflict: conflict.length ? {
    kind: "same_name_different_content" as const,
    existing_material_ids: conflict,
    actions: ["replace_as_version" as const, "keep_separate" as const],
  } : null,
});

test("real MaterialPanel renders authority filters, directory groups, and conflict actions", async () => {
  const fetcher = (async (input, init) => {
    const url = String(input);
    if (url.includes("/versions")) {
      assert.match(JSON.parse(String(init?.body)).source_material_id, /^(source|target)$/);
      return Response.json({ material: { material_id: "target" } });
    }
    return Response.json({
      materials: [summary("target", "primary", ["source"]), summary("source", "primary", ["target"])],
    });
  }) as typeof fetch;
  const mounted = render(<MaterialPanel researchId="project-a" fetcher={fetcher} />);
  await mounted.findByRole("combobox", { name: /资料类型筛选/ });
  assert.ok(mounted.getByRole("combobox", { name: /科学接纳状态筛选/ }));
  assert.ok(mounted.getByPlaceholderText("按标签筛选"));
  assert.ok(mounted.getByPlaceholderText("按目录筛选"));
  assert.ok(await mounted.findByRole("region", { name: "primary" }));
  const replace = mounted.getAllByRole("button", { name: /作为新版本/ })[0]!;
  fireEvent.click(replace);
  await waitFor(() => assert.ok(mounted.getAllByText("保留为独立资料").length > 0));
  mounted.unmount();
});

test("MaterialPanel refreshes processing materials until the authoritative status is ready", async () => {
  let calls = 0;
  const fetcher = (async () => {
    calls += 1;
    const row = summary("polling", "primary");
    row.current_revision.processing_status = calls === 1 ? "parsing" : "ready";
    return Response.json({ materials: [row] });
  }) as typeof fetch;
  const mounted = render(<MaterialPanel researchId="project-a" fetcher={fetcher} />);
  await mounted.findByText("解析中");
  await waitFor(() => assert.ok(calls >= 2), { timeout: 1800 });
  await waitFor(() => assert.ok(mounted.getAllByText("可供审查").length >= 2));
  mounted.unmount();
});

test("MaterialPanel discovers a newly uploaded material from an initially empty library", async () => {
  let calls = 0;
  const fetcher = (async () => {
    calls += 1;
    const uploaded = summary("new-upload", "primary");
    uploaded.display_name = "new-upload.csv";
    return Response.json({
      materials: calls === 1 ? [] : [uploaded],
    });
  }) as typeof fetch;
  const mounted = render(<MaterialPanel researchId="project-a" fetcher={fetcher} />);
  await mounted.findByText("项目资料库中还没有资料。");
  window.dispatchEvent(new window.CustomEvent("d18:material-library-changed", {
    detail: { researchId: "project-a" },
  }));
  await mounted.findByText("new-upload.csv");
  assert.ok(calls >= 2);
  mounted.unmount();
});

test("real MaterialPreview highlights the excerpt selected by its locator", async () => {
  const fetcher = (async (input) => {
    const highlighted = String(input).includes("highlight_locator_id=locator-a");
    return Response.json({
      material_id: "material-a",
      revision_id: "revision-a",
      processing_status: "ready",
      scientific_status: "candidate_only",
      preview: {
        kind: "text",
        excerpts: [
          { escaped_text: "alpha evidence", source_span: { kind: "line_range", locator: { start_line: 1, end_line: 1 } } },
          { escaped_text: "beta evidence", source_span: { kind: "line_range", locator: { start_line: 2, end_line: 2 } } },
        ],
      },
      locators: [{ locator_id: "locator-a", revision_id: "revision-a", locator_kind: "line_range", locator: { start_line: 1, end_line: 1 }, scientific_status: "candidate" }],
      highlight: highlighted ? { locator_id: "locator-a", revision_id: "revision-a", locator_kind: "line_range", locator: { start_line: 1, end_line: 1 }, scientific_status: "candidate" } : null,
    });
  }) as typeof fetch;
  const mounted = render(<MaterialPreview researchId="project-a" materialId="material-a" fetcher={fetcher} />);
  fireEvent.click(await mounted.findByRole("button", { name: /start_line/ }));
  await waitFor(() => assert.equal(mounted.getByText("alpha evidence").getAttribute("data-highlighted"), "true"));
  assert.equal(mounted.getByText("beta evidence").hasAttribute("data-highlighted"), false);
  mounted.unmount();
});

test("landing preserves a completed material when a later file fails and retry sends only the failed file", async () => {
  const first = new File(["first"], "first.txt", { type: "text/plain" });
  const second = new File(["second"], "second.txt", { type: "text/plain" });
  const attempts: Array<{ files: string[]; materialIds: string[] }> = [];
  let call = 0;
  const mounted = render(
    <HeroInput
      creating={false}
      onSubmit={async (_text, files, _scope, materialIds, onProgress) => {
        attempts.push({ files: files.map((file) => file.name), materialIds });
        call += 1;
        if (call === 1) {
          onProgress({ file: first, status: "uploaded", materialId: "material-first" });
          onProgress({ file: second, status: "failed", error: "second upload failed" });
          throw new Error("second upload failed");
        }
        onProgress({ file: second, status: "uploaded", materialId: "material-second" });
      }}
    />,
  );
  fireEvent.change(mounted.getByLabelText("Upload files"), { target: { files: [first, second] } });
  fireEvent.submit(mounted.container.querySelector("form")!);
  await mounted.findByText("second upload failed");
  assert.match(
    mounted.getByText("first.txt").parentElement?.textContent ?? "",
    /已提交至项目资料库/,
  );
  fireEvent.click(mounted.getByRole("button", { name: "重试" }));
  await waitFor(() => assert.equal(attempts.length, 2));
  assert.deepEqual(attempts, [
    { files: ["first.txt", "second.txt"], materialIds: [] },
    { files: ["second.txt"], materialIds: ["material-first"] },
  ]);
  mounted.unmount();
});

test("AttachmentTray renders retry remove omit and reselect controls for actual states", () => {
  const mounted = render(
    <PromptInputProvider
      initialAttachments={[
        { id: "failed", type: "file", url: "", mediaType: "text/plain", filename: "failed.txt", uploadStatus: "failed" },
        { id: "restored", type: "file", url: "", mediaType: "text/plain", filename: "restored.txt", uploadStatus: "reselect_required" },
      ]}
    >
      <AttachmentTray onRetry={() => {}} onReselect={() => {}} />
    </PromptInputProvider>,
  );
  for (const label of ["重试", "重新选择同一文件以继续上传"]) {
    assert.ok(mounted.getByRole("button", { name: label }));
  }
  assert.equal(mounted.getAllByRole("button", { name: "本次不使用" }).length, 2);
  assert.equal(mounted.getAllByRole("button", { name: "移除" }).length, 2);
  mounted.unmount();
});

test("failed attachment blocks the real Composer until explicit omit and then sends no subset", async () => {
  Object.assign(globalThis, { indexedDB: memoryIndexedDb([{
    projectId: "project-omit",
    uploadId: "failed-upload",
    fingerprint: "failed.txt:4:1",
    filename: "failed.txt",
    size: 4,
    lastModified: 1,
    expectedSha256: "a".repeat(64),
    status: "failed",
    partSize: 8 * 1024 * 1024,
  }]) });
  const sent: Array<{ text: string; materialIds: string[] }> = [];
  const mounted = render(
    <Composer disabled={false} draftId="project-omit" onSend={(submission) => sent.push(submission)} placeholder="objective-omit" />,
  );
  await mounted.findByPlaceholderText("objective-omit");
  fireEvent.change(mounted.getByPlaceholderText("objective-omit"), { target: { value: "inspect" } });
  const submit = mounted.container.querySelector("button[type='submit']") as HTMLButtonElement;
  fireEvent.click(submit);
  await waitFor(() => assert.equal(sent.length, 0));
  fireEvent.click(mounted.getByRole("button", { name: "本次不使用" }));
  fireEvent.click(submit);
  await waitFor(() => assert.equal(sent.length, 1));
  assert.deepEqual(sent[0]?.materialIds, []);
  mounted.unmount();
});

test("restored Composer reselect verifies and resumes through status before only missing parts", async () => {
  const sha = "a".repeat(64);
  Object.assign(globalThis, { indexedDB: memoryIndexedDb([{
    projectId: "project-resume",
    uploadId: "resume-upload",
    fingerprint: "resume.txt:4:123",
    filename: "resume.txt",
    size: 4,
    lastModified: 123,
    expectedSha256: sha,
    status: "uploading",
    partSize: 8 * 1024 * 1024,
  }]) });
  const originalFetch = globalThis.fetch;
  const originalWorker = globalThis.Worker;
  const calls: Array<{ method: string; url: string; body?: string }> = [];
  Object.assign(globalThis, {
    Worker: class {
      onmessage: ((event: MessageEvent) => void) | null = null;
      postMessage() {
        queueMicrotask(() => this.onmessage?.({ data: { type: "complete", sha256: sha, parts: [{ partNumber: 0, sha256: sha }] } } as MessageEvent));
      }
      terminate() {}
    },
  });
  globalThis.fetch = async (input, init) => {
    const method = init?.method ?? "GET";
    const url = String(input);
    calls.push({ method, url, body: typeof init?.body === "string" ? init.body : undefined });
    if (method === "GET") return Response.json({ upload: { parts: [] } });
    if (url.endsWith("/complete")) return Response.json({ material: { material_id: "material-resumed" }, revision: { processing_status: "pending_security" } });
    return Response.json({ ok: true });
  };
  try {
    const mounted = render(
      <Composer disabled={false} draftId="project-resume" onSend={() => {}} placeholder="objective-resume" />,
    );
    await mounted.findByPlaceholderText("objective-resume");
    mounted.getByRole("button", { name: /资料范围.*整个研究项目/ });
    fireEvent.click(mounted.getByRole("button", { name: "重新选择同一文件以继续上传" }));
    fireEvent.change(mounted.container.querySelector("input[aria-label='重新选择同一文件以继续上传']")!, {
      target: { files: [new File(["same"], "resume.txt", { type: "text/plain", lastModified: 123 })] },
    });
    await waitFor(() => assert.ok(calls.some((call) => call.url.endsWith("/complete"))));
    assert.deepEqual(calls.map((call) => `${call.method} ${call.url}`), [
      "GET /api/d18/material-uploads/resume-upload",
      "PUT /api/d18/material-uploads/resume-upload/parts/0",
      "POST /api/d18/material-uploads/resume-upload/complete",
    ]);
    assert.equal(JSON.parse(calls[2]?.body ?? "{}").scope, "project");
    mounted.unmount();
  } finally {
    globalThis.fetch = originalFetch;
    Object.assign(globalThis, { Worker: originalWorker });
  }
});

test("mismatched restored file remains on the original session and requires reselect again", async () => {
  const expectedSha = "a".repeat(64);
  Object.assign(globalThis, { indexedDB: memoryIndexedDb([{
    projectId: "project-mismatch",
    uploadId: "original-upload",
    fingerprint: "expected.txt:4:123",
    filename: "expected.txt",
    size: 4,
    lastModified: 123,
    expectedSha256: expectedSha,
    status: "uploading",
    partSize: 8 * 1024 * 1024,
  }]) });
  const originalFetch = globalThis.fetch;
  const originalWorker = globalThis.Worker;
  const calls: string[] = [];
  let hash = expectedSha;
  Object.assign(globalThis, {
    Worker: class {
      onmessage: ((event: MessageEvent) => void) | null = null;
      postMessage() {
        queueMicrotask(() => this.onmessage?.({ data: { type: "complete", sha256: hash, parts: [{ partNumber: 0, sha256: hash }] } } as MessageEvent));
      }
      terminate() {}
    },
  });
  globalThis.fetch = async (input, init) => {
    const method = init?.method ?? "GET";
    calls.push(`${method} ${input}`);
    if (method === "GET") return Response.json({ upload: { parts: [{ part_number: 0 }] } });
    if (String(input).endsWith("/complete")) return Response.json({ material: { material_id: "material-original" }, revision: { processing_status: "pending_security" } });
    return Response.json({ ok: true });
  };
  try {
    const mounted = render(
      <Composer disabled={false} draftId="project-mismatch" onSend={() => {}} placeholder="objective-mismatch" />,
    );
    await mounted.findByPlaceholderText("objective-mismatch");
    fireEvent.click(mounted.getByRole("button", { name: "重新选择同一文件以继续上传" }));
    const reselectInput = mounted.container.querySelector("input[aria-label='重新选择同一文件以继续上传']")!;
    fireEvent.change(reselectInput, {
      target: { files: [new File(["same"], "wrong.txt", { type: "text/plain", lastModified: 123 })] },
    });
    await waitFor(() => assert.match(mounted.getByRole("alert").textContent ?? "", /fingerprint/));
    assert.equal(calls.length, 0);
    assert.ok(mounted.getByRole("button", { name: "重新选择同一文件以继续上传" }));

    hash = expectedSha;
    fireEvent.change(reselectInput, {
      target: { files: [new File(["same"], "expected.txt", { type: "text/plain", lastModified: 123 })] },
    });
    await waitFor(() => assert.ok(calls.some((call) => call.endsWith("/complete"))));
    assert.equal(calls[0], "GET /api/d18/material-uploads/original-upload");
    assert.equal(calls.some((call) => call.includes("/research/") || call.includes("material-uploads/original-upload/parts")), false);
    mounted.unmount();
  } finally {
    globalThis.fetch = originalFetch;
    Object.assign(globalThis, { Worker: originalWorker });
  }
});
