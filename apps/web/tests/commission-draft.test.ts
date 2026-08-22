import assert from "node:assert/strict";
import test from "node:test";

import {
  compileCommissionObjective,
  commissionHasObjective,
  createCommissionDraft,
} from "../src/lib/commission-draft.ts";

test("a selected task creates an editable draft without runtime routing data", () => {
  const draft = createCommissionDraft("goal-and-scope");
  assert.match(draft.objective, /镀锌钢制检修口/);
  assert.equal(draft.taskId, "goal-and-scope");
  assert.equal("route" in draft, false);
  assert.equal("skill" in draft, false);
});

test("professional fields compile into one readable outcome contract", () => {
  const draft = createCommissionDraft();
  draft.objective = "比较两个包装方案的环境表现";
  draft.studyObject = "1,000 次包装交付服务";
  draft.deliverable = "比较表和结论翻转条件";
  draft.professional.functionalUnit = {
    state: "supplied",
    value: "1,000 次包装交付",
  };
  draft.professional.systemBoundary = {
    state: "agent_may_determine",
    value: "",
  };
  draft.professional.lciaMethod = { state: "unknown", value: "" };

  const objective = compileCommissionObjective(draft);
  assert.match(objective, /研究目标：比较两个包装方案/);
  assert.match(objective, /研究对象：1,000 次包装交付服务/);
  assert.match(objective, /功能单位：1,000 次包装交付/);
  assert.match(objective, /系统边界：交由 Agent 根据研究目的判断/);
  assert.match(objective, /LCIA 方法：目前未知/);
  assert.match(objective, /期望交付：比较表和结论翻转条件/);
});

test("not-applicable and blank supplied values are omitted or rejected", () => {
  const draft = createCommissionDraft();
  assert.equal(commissionHasObjective(draft), false);
  draft.objective = "解释功能单位如何改变结论";
  draft.professional.comparisonBasis = {
    state: "not_applicable",
    value: "",
  };
  draft.professional.functionalUnit = { state: "supplied", value: "" };
  assert.throws(
    () => compileCommissionObjective(draft),
    /supplied field is empty/,
  );
});
