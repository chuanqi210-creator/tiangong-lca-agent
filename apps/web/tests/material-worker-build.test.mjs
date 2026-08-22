import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("production material hash worker is bundled browser JavaScript", async () => {
  const body = await readFile(new URL("../public/material-hash.worker.js", import.meta.url), "utf8");
  assert.match(body, /addEventListener\(["']message["']/);
  assert.doesNotMatch(body, /@noble\/hashes/);
  assert.doesNotMatch(body, /MessageEvent<|interface\s+|:\s*File\b/);
});
