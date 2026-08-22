import assert from "node:assert/strict";
import test from "node:test";

import {
  RESEARCH_TASKS,
  getResearchTask,
  isResearchTaskId,
  taskLibrarySections,
} from "../src/lib/research-task-library.ts";
import { copy } from "../src/lib/copy.ts";

test("the task library covers nine distinct scientific outcomes", () => {
  assert.equal(RESEARCH_TASKS.length, 9);
  assert.equal(new Set(RESEARCH_TASKS.map((task) => task.id)).size, 9);
  assert.deepEqual(
    new Set(RESEARCH_TASKS.map((task) => task.outcomeKind)),
    new Set([
      "goal_and_scope",
      "official_data",
      "inventory_gap",
      "calculation",
      "comparison_uncertainty",
      "interpretation",
      "review",
      "enterprise_decision",
      "learning",
    ]),
  );
});

test("every task is useful without exposing an implementation route", () => {
  const forbiddenKeys = new Set([
    "route",
    "skill",
    "provider",
    "model",
    "capability",
  ]);
  for (const task of RESEARCH_TASKS) {
    assert.ok(task.title.trim());
    assert.ok(task.useWhen.trim());
    assert.ok(task.minimumInput.length > 0);
    assert.ok(task.agentWork.length > 0);
    assert.ok(task.deliverables.length > 0);
    assert.ok(task.boundary.trim());
    assert.ok(task.exampleObjective.trim());
    for (const key of Object.keys(task)) {
      assert.equal(forbiddenKeys.has(key), false);
    }
  }
});

test("lookups fail closed for unknown task ids", () => {
  assert.equal(isResearchTaskId("official-data"), true);
  assert.equal(isResearchTaskId("lifepo4-route"), false);
  assert.equal(getResearchTask("official-data").outcomeKind, "official_data");
  assert.throws(() => getResearchTask("not-a-task"), /unknown research task/);
});

test("the professional matrix leads and learning stays a secondary section", () => {
  const sections = taskLibrarySections();
  assert.equal(sections[0].id, "professional");
  assert.equal(sections[0].tasks.length, 8);
  assert.equal(sections[1].id, "learning");
  assert.deepEqual(
    sections[1].tasks.map((task) => task.id),
    ["guided-learning"],
  );
  assert.match(copy.taskLibrary.body, /不会替你提交/);
  assert.match(copy.taskLibrary.body, /处理路线/);
});
