import assert from "node:assert/strict";
import test from "node:test";

import {
  attachmentSubmissionDecision,
  canSubmitComposer,
} from "../src/lib/d18/material-types.ts";

test("attachment-only composer is submittable only after every selected file is uploaded or omitted", () => {
  assert.equal(canSubmitComposer("", [{ status: "uploaded", materialId: "m1", omitted: false }]), true);
  assert.equal(canSubmitComposer("", [{ status: "failed", omitted: false }]), false);
  assert.equal(canSubmitComposer("", [{ status: "failed", omitted: true }]), true);
  assert.equal(canSubmitComposer("inspect", [{ status: "reselect_required", omitted: false }]), false);
});

test("failed attachment exposes retry remove and omit without silently sending a subset", () => {
  assert.deepEqual(attachmentSubmissionDecision([
    { status: "uploaded", materialId: "m1", omitted: false },
    { status: "failed", omitted: false },
  ]), { ready: false, materialIds: [], blockedActions: ["retry", "remove", "omit"] });
});

test("a failed attachment can be explicitly omitted without dropping completed materials", () => {
  assert.deepEqual(attachmentSubmissionDecision([
    { status: "uploaded", materialId: "m1", omitted: false },
    { status: "failed", omitted: true },
  ]), { ready: true, materialIds: ["m1"], blockedActions: [] });
});
