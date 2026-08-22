import assert from "node:assert/strict";
import test from "node:test";

import {
  citationLocatorId,
  evidenceBffUrl,
} from "../src/lib/d18/client.ts";
import { citationLocatorLabel } from "../src/lib/d18/citations.ts";
import type { EvidenceCitation } from "../src/lib/d18/types.ts";

function citation(locator: EvidenceCitation["locator"]): EvidenceCitation {
  return {
    citationId: "cit_0123456789abcdef01234567",
    materialId: "material/a",
    revisionId: "revision/a",
    displayName: "source.pdf",
    locator,
    evidenceUrl: "/v1/research/project%2Fa/evidence/locator%2Fa",
    sourceAuthority: "user_supplied_project_input",
    processingStatus: "ready",
  };
}

test("exact evidence URL is normalized to the same-origin BFF and locator identity", () => {
  const value = citation({ kind: "pdf_region", page: 3, bbox: [0.1, 0.2, 0.8, 0.4] });
  assert.equal(citationLocatorId(value.evidenceUrl), "locator/a");
  assert.equal(
    evidenceBffUrl(value.evidenceUrl),
    "/api/d18/research/project%2Fa/evidence/locator%2Fa",
  );
  assert.throws(() => evidenceBffUrl("https://evil.invalid/evidence/locator"), /evidence URL/);
  assert.throws(() => citationLocatorId("/v1/files/secret"), /evidence URL/);
});

test("citation labels retain exact supported material locations", () => {
  const cases: Array<[EvidenceCitation["locator"], RegExp]> = [
    [{ kind: "pdf_region", page: 3, normalized_rect: [0, 0, 1, 1] }, /第 3 页/],
    [{ kind: "document_section", heading_path: ["Methods"], paragraph_start: 8, paragraph_end: 9 }, /Methods.*段落 8–9/],
    [{ kind: "spreadsheet_range", sheet_name: "Inventory", cell_range: "B3:D7" }, /Inventory.*B3:D7/],
    [{ kind: "text_span", start_line: 4, end_line: 6 }, /第 4–6 行/],
    [{ kind: "json_pointer", pointer: "/exchanges/0/amount" }, /\/exchanges\/0\/amount/],
    [{ kind: "xml_xpath", xpath: "/process/exchange[1]" }, /process\/exchange/],
    [{ kind: "archive_member", member_path: "datasets/process.xml" }, /datasets\/process\.xml/],
    [{ kind: "lca_object", dataset_uuid: "uuid-1", version: "2.1", provider: "TianGong" }, /uuid-1.*2\.1.*TianGong/],
  ];
  for (const [locator, expected] of cases) {
    assert.match(citationLocatorLabel(citation(locator)), expected);
  }
});
