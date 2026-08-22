import assert from "node:assert/strict";
import test from "node:test";

import { getProjectMaterialPreview, reuseProjectOutput } from "../src/lib/d18/material-client.ts";

test("safe preview requests exact locator highlighting through the BFF", async () => {
  const calls: string[] = [];
  const fetcher = (async (input) => {
    calls.push(String(input));
    return Response.json({
      material_id: "material-a",
      revision_id: "revision-a",
      preview: { kind: "text", excerpts: [] },
      locators: [],
      highlight: { locator_id: "locator/a" },
    });
  }) as typeof fetch;
  const result = await getProjectMaterialPreview(
    "project/a",
    "material/a",
    "locator/a",
    fetcher,
  );
  assert.equal(result.highlight?.locator_id, "locator/a");
  assert.equal(
    calls[0],
    "/api/d18/research/project%2Fa/materials/material%2Fa/preview?highlight_locator_id=locator%2Fa",
  );
});

test("accepted output reuse is an idempotent server command", async () => {
  let captured: RequestInit | undefined;
  const fetcher = (async (_input, init) => {
    captured = init;
    return Response.json({ material: { material_id: "new-material" } }, { status: 201 });
  }) as typeof fetch;
  await reuseProjectOutput("p", "artifact-a", "project", "reuse-key", fetcher);
  assert.equal(new Headers(captured?.headers).get("Idempotency-Key"), "reuse-key");
  assert.deepEqual(JSON.parse(String(captured?.body)), {
    artifact_id: "artifact-a",
    scope: "project",
  });
});
