import assert from "node:assert/strict";
import test from "node:test";

import {
  listProjectMaterials,
  materialListPath,
  updateProjectMaterialScope,
  withdrawProjectMaterial,
} from "../src/lib/d18/material-client.ts";

test("material list/search filters remain server-authoritative", async () => {
  assert.equal(
    materialListPath("project a", {
      query: "inventory",
      typeFamily: "table",
      processingStatus: "ready",
      scientificStatus: "accepted_project_input",
      scope: "project",
      tag: "supplier",
      directory: "primary",
      deleted: "all",
    }),
    "/api/d18/research/project%20a/materials?query=inventory&type_family=table&processing_status=ready&scientific_status=accepted_project_input&scope=project&tag=supplier&directory=primary&deleted=all",
  );
  const calls: Array<[string, RequestInit | undefined]> = [];
  const fetcher = (async (input, init) => {
    calls.push([String(input), init]);
    return Response.json({ materials: [{ material_id: "m-a" }] });
  }) as typeof fetch;
  const result = await listProjectMaterials("project-a", { query: "a" }, fetcher);
  assert.deepEqual(result, { materials: [{ material_id: "m-a" }] });
  assert.equal(calls[0]?.[0], "/api/d18/research/project-a/materials?query=a");
  assert.equal(calls[0]?.[1]?.cache, "no-store");
});

test("scope and withdraw commands carry idempotency without local scientific state", async () => {
  const calls: Array<[string, RequestInit | undefined]> = [];
  const fetcher = (async (input, init) => {
    calls.push([String(input), init]);
    return Response.json({ material: { material_id: "m-a" } });
  }) as typeof fetch;
  await updateProjectMaterialScope("p", "m-a", "project", "scope-key", fetcher);
  await withdrawProjectMaterial("p", "m-a", "delete-key", fetcher);
  assert.equal(new Headers(calls[0]?.[1]?.headers).get("Idempotency-Key"), "scope-key");
  assert.deepEqual(JSON.parse(String(calls[0]?.[1]?.body)), { scope: "project" });
  assert.equal(calls[1]?.[1]?.method, "DELETE");
  assert.equal(new Headers(calls[1]?.[1]?.headers).get("Idempotency-Key"), "delete-key");
});
