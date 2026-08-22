import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const landingSource = readFileSync("src/app/page.tsx", "utf8");

test("landing creates research inside the active backend session namespace", () => {
  assert.match(landingSource, /connectD18Session/);
  assert.match(landingSource, /createResearchForNamespace/);
  assert.doesNotMatch(landingSource, /\bcreateResearch\(/);
});

test("landing exposes backend connection state before research creation", () => {
  assert.match(landingSource, /connection\.status === "connected"/);
  assert.match(landingSource, /connectionUnavailable/);
  assert.match(landingSource, /connectionRetry/);
});
