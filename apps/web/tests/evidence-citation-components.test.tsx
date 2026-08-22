import assert from "node:assert/strict";
import test from "node:test";

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { Window } from "happy-dom";
import React from "react";

import { ResultCard } from "../src/components/chat/ResultCard.tsx";
import type { ResultCardData } from "../src/components/chat/types.ts";

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

test("result citation opens the project-scoped preview at the exact locator", async () => {
  const calls: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input) => {
    calls.push(String(input));
    return Response.json({
      material_id: "material-a",
      revision_id: "revision-a",
      processing_status: "ready",
      scientific_status: "candidate_only",
      preview: {
        kind: "spreadsheet",
        rows: [{
          cells: [{ displayed_value: "42" }],
          source_span: {
            kind: "spreadsheet_range",
            locator: { sheet_name: "Table1", cell_range: "A2:C2" },
          },
        }],
      },
      locators: [],
      highlight: {
        locator_id: "locator-a",
        revision_id: "revision-a",
        locator_kind: "spreadsheet_range",
        locator: { sheet_name: "Table1", cell_range: "A2:C2" },
        scientific_status: "accepted_evidence",
      },
    });
  }) as typeof fetch;
  const card: ResultCardData = {
    kind: "result",
    id: "result-a",
    variant: "normal",
    title: "结果",
    researchId: "project-a",
    layers: [
      { body: ["结论"] },
      { body: ["依据"] },
      { body: ["限制"] },
      { body: ["成果"] },
    ],
    citations: [{
      citationId: "cit_0123456789abcdef01234567",
      materialId: "material-a",
      revisionId: "revision-a",
      displayName: "inventory.csv",
      locator: { kind: "spreadsheet_range", sheet_name: "Table1", cell_range: "A2:C2" },
      evidenceUrl: "/v1/research/project-a/evidence/locator-a",
      sourceAuthority: "user_supplied_project_input",
      processingStatus: "ready",
    }],
    sourceUse: [{
      materialId: "material-a",
      revisionId: "revision-a",
      displayName: "inventory.csv",
      status: "used",
      reason: "cited_in_accepted_result",
      sourceAuthority: "user_supplied_project_input",
      processingStatus: "ready",
      selectedLocatorCount: 1,
      citedLocatorCount: 1,
    }],
    materialLimitations: [],
  };
  try {
    const mounted = render(<ResultCard card={card} />);
    assert.match(mounted.getByRole("button", { name: /打开精确来源/ }).textContent ?? "", /^\[1\]/);
    fireEvent.click(mounted.getByRole("button", { name: /打开精确来源/ }));
    await waitFor(() => assert.equal(
      calls[0],
      "/api/d18/research/project-a/materials/material-a/preview?highlight_locator_id=locator-a",
    ));
    await waitFor(() => assert.ok(mounted.container.querySelector("[data-highlighted='true']")));
    assert.match(mounted.getByText("已采用").parentElement?.textContent ?? "", /inventory\.csv/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("official Project Record sources render as verifiable browser links", () => {
  const card: ResultCardData = {
    kind: "result",
    id: "result-official",
    variant: "normal",
    title: "结果",
    researchId: "project-official",
    layers: [
      { body: ["结论"] },
      { body: ["官方来源元数据"] },
      { body: ["限制"] },
      { body: ["成果"] },
    ],
    citations: [],
    sourceUse: [],
    materialLimitations: [],
    officialSources: [{
      sourceId: "eu-ir-2025-2547",
      authority: "official_public_source",
      sourceAuthority: "Official Journal and EUR-Lex",
      requestedUrl: "https://eur-lex.europa.eu/source",
      finalUrl: "https://eur-lex.europa.eu/eli/reg_impl/2025/2547/oj/eng",
      accessedAt: "2026-08-17T08:00:00Z",
      bodySha256: "a".repeat(64),
      receiptSha256: "b".repeat(64),
      parseLocation: "functional unit",
      sourceVersion: "2025/2547",
      snapshotVersion: `sha256:${"a".repeat(64)}`,
    }],
  };

  const mounted = render(<ResultCard card={card} />);
  const link = mounted.getByRole("link", {
    name: /eu-ir-2025-2547.*functional unit/,
  });
  assert.equal(
    link.getAttribute("href"),
    "https://eur-lex.europa.eu/eli/reg_impl/2025/2547/oj/eng",
  );
  assert.equal(link.getAttribute("target"), "_blank");
  assert.equal(link.getAttribute("rel"), "noreferrer noopener");
});
