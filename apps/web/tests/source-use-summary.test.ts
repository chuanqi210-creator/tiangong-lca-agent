import assert from "node:assert/strict";
import test from "node:test";

import { groupSourceUse, sourceAuthorityLabel } from "../src/lib/d18/source-use.ts";
import type { SourceUse } from "../src/lib/d18/types.ts";

function source(status: SourceUse["status"], name: string): SourceUse {
  return {
    materialId: `material-${name}`,
    revisionId: `revision-${name}`,
    displayName: name,
    status,
    reason: `${status}-reason`,
    sourceAuthority: status === "conflict" ? "tiangong_official_data" : "user_supplied_project_input",
    processingStatus: status === "unavailable_partial" ? "partial" : "ready",
    selectedLocatorCount: status === "available_not_used" ? 0 : 1,
    citedLocatorCount: status === "used" ? 1 : 0,
  };
}

test("source-use projection remains separated into four auditable categories", () => {
  const grouped = groupSourceUse([
    source("available_not_used", "considered.csv"),
    source("conflict", "official-conflict.json"),
    source("used", "used.pdf"),
    source("unavailable_partial", "partial.zip"),
  ]);
  assert.deepEqual(grouped.used.map((row) => row.displayName), ["used.pdf"]);
  assert.deepEqual(grouped.considered.map((row) => row.displayName), ["considered.csv"]);
  assert.deepEqual(grouped.partial.map((row) => row.displayName), ["partial.zip"]);
  assert.deepEqual(grouped.conflicts.map((row) => row.displayName), ["official-conflict.json"]);
});

test("TianGong official and user project authority remain visually distinguishable", () => {
  assert.equal(sourceAuthorityLabel("tiangong_official_data"), "TianGong 官方");
  assert.equal(sourceAuthorityLabel("user_supplied_project_input"), "用户项目材料");
  assert.equal(sourceAuthorityLabel("external_candidate"), "外部候选");
});
