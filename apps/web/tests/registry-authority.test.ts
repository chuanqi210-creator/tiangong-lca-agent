import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  pendingMaterialsAreComplete,
  sanitizeRegistryForStorage,
} from "../src/lib/d18/registry-storage.ts";
import * as registry from "../src/lib/registry.ts";
import { replaceWithAuthoritativeResult } from "../src/lib/d18/result-authority.ts";
import type { UIMessage } from "../src/components/chat/types.ts";

test("research recovery BFF binds the route to the signed project scope", () => {
  const route = readFileSync(
    new URL(
      "../src/app/api/d18/research/[researchId]/jobs/latest/route.ts",
      import.meta.url,
    ),
    "utf8",
  );
  assert.match(
    route,
    /\/v1\/research\/\$\{encodeURIComponent\(researchId\)\}\/jobs\/latest/,
  );
  assert.match(route, /authContextFromRequest\(request, researchId\)/);
  assert.match(route, /configuredSessionTokenResolver/);
});

test("backend authority can reconstruct a missing local research entry", () => {
  assert.equal(
    typeof (registry as Record<string, unknown>).restoreResearchFromAuthority,
    "function",
  );
  const restore = (registry as unknown as {
    restoreResearchFromAuthority: (value: unknown) => {
      researchId: string;
      objective: string;
      jobId: string | null;
      status: string | null;
      userMessages: string[];
    };
  }).restoreResearchFromAuthority;
  const entry = restore({
    contract: "d18.public_research_recovery/v1",
    research_id: "pilot-a-research-1",
    objective: "解释这个材料。",
    user_messages: ["解释这个材料。"],
    job: {
      job_id: "job-1",
      status: "completed",
      project_id: "pilot-a-research-1",
      created_at: "2026-08-17T01:00:00Z",
      updated_at: "2026-08-17T01:00:05Z",
      runtime_release: null,
    },
  });
  assert.equal(entry.researchId, "pilot-a-research-1");
  assert.equal(entry.objective, "解释这个材料。");
  assert.equal(entry.jobId, "job-1");
  assert.equal(entry.status, "completed");
  assert.deepEqual(entry.userMessages, ["解释这个材料。"]);
});

test("complete user materials stay process-local and never enter localStorage", () => {
  const sanitized = sanitizeRegistryForStorage({
    research: {
      objective: "secure material",
      pendingSourceMaterials: [
      {
        name: "secret.txt",
        media_type: "text/plain",
        sha256: "a".repeat(64),
        authority: "user_supplied_project_input",
        content: { text: "full pending source material" },
      },
      ],
    },
  });
  const serialized = JSON.stringify(sanitized);
  assert.doesNotMatch(serialized, /full pending source material|pendingSourceMaterials/);
});

test("refresh loss of pending material bytes fails closed instead of submitting an empty task", () => {
  assert.equal(pendingMaterialsAreComplete(2, 0), false);
  assert.equal(pendingMaterialsAreComplete(2, 2), true);
});

test("authoritative Project Record result replaces a stale cached card", () => {
  const cached: UIMessage = {
    id: "cached",
    kind: "card",
    card: { kind: "notice", id: "result-job-1", title: "stale", body: "stale", tone: "recoverable" },
    handled: true,
  };
  const authoritative: Extract<UIMessage, { kind: "card" }> = {
    id: "fresh",
    kind: "card",
    card: { kind: "notice", id: "result-job-1", title: "accepted", body: "accepted", tone: "recoverable" },
    handled: true,
  };
  const next = replaceWithAuthoritativeResult([cached], "result-job-1", authoritative);
  assert.equal(next.length, 1);
  assert.equal(next[0].kind === "card" ? next[0].card.title : "", "accepted");
});

test("authoritative terminal result closes the matching restored input card", () => {
  const gap: UIMessage = {
    id: "gap-message",
    kind: "card",
    card: {
      kind: "gap_question",
      id: "gap-job-1",
      title: "missing facts",
      why: "supply facts",
      accept: "text",
    },
    handled: false,
  };
  const authoritative: Extract<UIMessage, { kind: "card" }> = {
    id: "fresh",
    kind: "card",
    card: {
      kind: "notice",
      id: "result-job-1",
      title: "accepted",
      body: "accepted",
      tone: "recoverable",
    },
    handled: true,
  };

  const next = replaceWithAuthoritativeResult(
    [gap],
    "result-job-1",
    authoritative,
    "gap-job-1",
  );

  assert.equal(next.length, 2);
  assert.equal(next[0].kind === "card" && next[0].handled, true);
  assert.equal(next[1].kind === "card" ? next[1].card.title : "", "accepted");
});
