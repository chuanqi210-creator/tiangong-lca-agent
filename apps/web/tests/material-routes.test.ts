import assert from "node:assert/strict";
import test from "node:test";

import { PUT as putPart } from "../src/app/api/d18/material-uploads/[uploadId]/parts/[partNumber]/route.ts";
import { GET as listMaterials } from "../src/app/api/d18/research/[researchId]/materials/route.ts";
import { GET as recoverResearch } from "../src/app/api/d18/research/[researchId]/jobs/latest/route.ts";
import { POST as addVersion } from "../src/app/api/d18/research/[researchId]/materials/[materialId]/versions/route.ts";

const ORIGINAL_ENV = { ...process.env };

test.afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
});

test("material part route fails closed in fixture mode before consuming the browser body", async () => {
  delete process.env.D18_BASE_URL;
  const request = new Request("http://localhost/api/d18/material-uploads/u/parts/0", {
    method: "PUT",
    headers: {
      "Content-Range": "bytes 0-3/4",
      "X-Part-SHA256": "a".repeat(64),
    },
    body: new ReadableStream({
      pull(controller) {
        controller.enqueue(new Uint8Array([1, 2, 3, 4]));
        controller.close();
      },
    }),
    duplex: "half",
  } as RequestInit & { duplex: "half" });
  const response = await putPart(request, {
    params: Promise.resolve({ uploadId: "u", partNumber: "0" }),
  });
  assert.equal(response.status, 503);
  assert.equal((await response.json()).code, "uploads_require_live_service");
  assert.equal(request.bodyUsed, false);
});

test("material list BFF forwards all authority filters and session context", async () => {
  process.env.D18_BASE_URL = "https://service.example.invalid";
  process.env.D18_AUTH_MODE = "local_static";
  process.env.D18_TOKEN = "server-token";
  const originalFetch = globalThis.fetch;
  const calls: Array<[string, RequestInit | undefined]> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push([String(input), init]);
    return Response.json({ materials: [] });
  }) as typeof fetch;
  try {
    const response = await listMaterials(
      new Request("http://localhost/api/d18/research/project-a/materials?type_family=table&scientific_status=accepted_project_input&tag=supplier&directory=primary"),
      { params: Promise.resolve({ researchId: "project-a" }) },
    );
    assert.equal(response.status, 200);
    assert.equal(
      calls[0]?.[0],
      "https://service.example.invalid/v1/research/project-a/materials?type_family=table&scientific_status=accepted_project_input&tag=supplier&directory=primary",
    );
    assert.equal(new Headers(calls[0]?.[1]?.headers).get("Authorization"), "Bearer server-token");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("research recovery BFF reads the latest backend job in project scope", async () => {
  process.env.D18_BASE_URL = "https://service.example.invalid";
  process.env.D18_AUTH_MODE = "local_static";
  process.env.D18_TOKEN = "server-token";
  const originalFetch = globalThis.fetch;
  let captured: [string, RequestInit | undefined] | undefined;
  globalThis.fetch = (async (input, init) => {
    captured = [String(input), init];
    return Response.json({
      contract: "d18.public_research_recovery/v1",
      research_id: "project-a",
      objective: "explain",
      user_messages: ["explain"],
      job: { job_id: "job-a", status: "completed", project_id: "project-a" },
    });
  }) as typeof fetch;
  try {
    const response = await recoverResearch(
      new Request("http://localhost/api/d18/research/project-a/jobs/latest"),
      { params: Promise.resolve({ researchId: "project-a" }) },
    );
    assert.equal(response.status, 200);
    assert.equal(
      captured?.[0],
      "https://service.example.invalid/v1/research/project-a/jobs/latest",
    );
    assert.equal(
      new Headers(captured?.[1]?.headers).get("Authorization"),
      "Bearer server-token",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("material version BFF preserves the real conflict action and idempotency key", async () => {
  process.env.D18_BASE_URL = "https://service.example.invalid";
  process.env.D18_AUTH_MODE = "local_static";
  process.env.D18_TOKEN = "server-token";
  const originalFetch = globalThis.fetch;
  let captured: [string, RequestInit | undefined] | undefined;
  globalThis.fetch = (async (input, init) => {
    captured = [String(input), init];
    return Response.json({ material: { material_id: "target" } });
  }) as typeof fetch;
  try {
    const response = await addVersion(
      new Request("http://localhost/api/d18/research/project-a/materials/target/versions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": "replace-conflict" },
        body: JSON.stringify({ source_material_id: "source" }),
      }),
      { params: Promise.resolve({ researchId: "project-a", materialId: "target" }) },
    );
    assert.equal(response.status, 200);
    assert.equal(captured?.[0], "https://service.example.invalid/v1/research/project-a/materials/target/versions");
    assert.equal(new Headers(captured?.[1]?.headers).get("Idempotency-Key"), "replace-conflict");
    assert.deepEqual(JSON.parse(String(captured?.[1]?.body)), { source_material_id: "source" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
