import assert from "node:assert/strict";
import test from "node:test";

import { act, cleanup, render, waitFor } from "@testing-library/react";
import { Window } from "happy-dom";
import React from "react";

import {
  useResearchMessages,
  type ResearchMessagesApi,
} from "../src/lib/use-research-messages.ts";

const window = new Window({ url: "http://localhost" });
Object.assign(globalThis, {
  document: window.document,
  HTMLElement: window.HTMLElement,
  localStorage: window.localStorage,
  window,
});
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: window.navigator,
});

test.afterEach(() => cleanup());

function MessagesHarness({
  capture,
}: {
  capture: (api: ResearchMessagesApi) => void;
}) {
  const api = useResearchMessages("research-result-retry");
  capture(api);
  return (
    <div>
      {api.messages.map((message) =>
        message.kind === "card" ? message.card.title : null,
      )}
    </div>
  );
}

test("a transient first result response is retried before terminal UI settles", async () => {
  const originalFetch = globalThis.fetch;
  let requests = 0;
  globalThis.fetch = async () => {
    requests += 1;
    if (requests === 1) {
      return Response.json(
        { detail: "accepted result projection is temporarily unavailable" },
        { status: 502 },
      );
    }
    return Response.json({
      contract: "d18.public_result_projection/v1",
      research_id: "research-result-retry",
      status: "accepted",
      result: {
        status: "formal_result_accepted",
        claim_ceiling: "source_bounded_interpretation",
        answer: "已接纳的材料解释。",
        values: [],
        limitations: [],
        missing_fields: [],
        citations: [],
        sourceUse: [],
        materialLimitations: [],
      },
      attachments: [],
    });
  };

  let api!: ResearchMessagesApi;
  try {
    const mounted = render(
      <MessagesHarness capture={(value) => { api = value; }} />,
    );
    let settled = false;
    await act(async () => {
      settled = await api.settleResult({
        job_id: "job-result-retry",
        project_id: "research-result-retry",
        status: "completed",
      });
    });

    assert.equal(settled, true);
    assert.equal(requests, 2);
    await waitFor(() => {
      assert.match(mounted.container.textContent ?? "", /研究结果/);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
