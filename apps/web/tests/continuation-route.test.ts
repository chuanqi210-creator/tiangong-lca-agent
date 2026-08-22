import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("continuation BFF route forwards the exact backend endpoint", () => {
  const route = readFileSync(
    new URL(
      "../src/app/api/d18/jobs/[id]/continuations/route.ts",
      import.meta.url,
    ),
    "utf8",
  );
  assert.match(route, /\/v1\/jobs\/\$\{encodeURIComponent\(id\)\}\/continuations/);
  assert.match(route, /configuredSessionTokenResolver/);
});

test("fixture continuation stays terminal-only and content-bound", () => {
  const engine = readFileSync(
    new URL("../src/lib/d18/fixture-engine.ts", import.meta.url),
    "utf8",
  );
  assert.match(engine, /export function fxContinuation/);
  assert.match(
    engine,
    /continuation:\$\{parentJobId\}:\$\{inputId\}:\$\{sha\(text\)\}/,
  );
  assert.match(engine, /continuation requires a terminal parent job/);
});
