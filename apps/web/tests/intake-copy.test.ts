import assert from "node:assert/strict";
import test from "node:test";

import { copy } from "../src/lib/copy.ts";
import { RESEARCH_TASKS } from "../src/lib/research-task-library.ts";

test("the usage guide explains professional work, user facts, and valid endings", () => {
  const body = JSON.stringify(copy.usageGuide);
  for (const phrase of [
    "功能单位",
    "系统边界",
    "TianGong",
    "缺口",
    "受限结论",
    "可复核",
  ]) {
    assert.match(body, new RegExp(phrase));
  }
});

test("intake content does not expose runtime implementation choices", () => {
  const body = JSON.stringify({
    tasks: RESEARCH_TASKS,
    guide: copy.usageGuide,
  });
  for (const term of [
    "选择 Skill",
    "选择 Route",
    "选择供应商",
    "选择模型",
    "process UUID",
  ]) {
    assert.doesNotMatch(body, new RegExp(term, "i"));
  }
});
