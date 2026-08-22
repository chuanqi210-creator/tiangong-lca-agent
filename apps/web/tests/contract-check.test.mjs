import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  checkD18Bundle,
  checkD18Contract,
} from "../scripts/check-d18-contract.mjs";

const openapiPath = new URL(
  "../../../outputs/d18-service/integration/openapi-v1.json",
  import.meta.url,
);
const manifestPath = new URL(
  "../../../outputs/d18-service/integration/manifest.json",
  import.meta.url,
);

async function adoptedContract() {
  return JSON.parse(await readFile(openapiPath, "utf8"));
}

test("adopted backend contract exposes the complete frontend surface", async () => {
  assert.deepEqual(checkD18Contract(await adoptedContract()), []);
});

for (const [label, mutate] of [
  ["required route", (value) => delete value.paths["/v1/jobs/{job_id}/result"]],
  [
    "typed source materials",
    (value) => delete value.components.schemas.JobRequest.properties.source_materials,
  ],
  [
    "fixed user authority",
    (value) => delete value.components.schemas.SourceMaterialInput.properties.authority.const,
  ],
  [
    "complete job states",
    (value) => value.components.schemas.JobViewResponse.properties.status.enum.splice(0, 1),
  ],
  [
    "result summary",
    (value) => delete value.components.schemas.ResearchResultResponse.properties.summary,
  ],
  [
    "actual result response binding",
    (value) => delete value.paths["/v1/jobs/{job_id}/result"].get.responses["200"].content["application/json"].schema.$ref,
  ],
  [
    "raw public path",
    (value) => value.components.schemas.ContentRefResponse.properties.path = { type: "string" },
  ],
  [
    "registered attachment schema",
    (value) => delete value.components.schemas.PublicAttachmentResponse.properties.fileUrl,
  ],
  [
    "typed supplemental material union",
    (value) => value.components.schemas.JobInputRequest.properties.payload.anyOf.splice(0, 1),
  ],
  [
    "material upload route",
    (value) => delete value.paths["/v1/research/{research_id}/material-uploads"],
  ],
  [
    "Qingxiaoda audio content part",
    (value) => value.components.schemas.ChatMessage.properties.content.anyOf
      .find((item) => item.type === "array").items.oneOf
      .splice(2, 1),
  ],
  [
    "resumable upload header",
    (value) => value.paths["/v1/material-uploads/{upload_id}/parts/{part_number}"]
      .put.parameters = value.paths["/v1/material-uploads/{upload_id}/parts/{part_number}"]
      .put.parameters.filter((item) => item.name !== "Content-Range"),
  ],
  [
    "material state axis",
    (value) => delete value.components.schemas.MaterialRevisionResponse
      .properties.scientific_status,
  ],
  [
    "problem code",
    (value) => delete value.components.schemas.ProblemDetail.properties.code,
  ],
  [
    "material event stream",
    (value) => delete value.paths["/v1/research/{research_id}/material-events"]
      .get.responses["200"].content["text/event-stream"].schema["x-d18-event-contract"],
  ],
  [
    "citation locator",
    (value) => value.components.schemas.PublicCitationResponse.required =
      value.components.schemas.PublicCitationResponse.required.filter(
        (field) => field !== "locator",
      ),
  ],
  [
    "official source version binding",
    (value) => value.components.schemas.PublicOfficialSourceResponse.required =
      value.components.schemas.PublicOfficialSourceResponse.required.filter(
        (field) => field !== "sourceVersion",
      ),
  ],
  [
    "official claim source binding",
    (value) => delete value.components.schemas.ResearchResultResponse
      .properties.officialClaimBindings,
  ],
  [
    "structured waiting question",
    (value) => delete value.components.schemas.InputRequiredResponse
      .properties.question_count,
  ],
]) {
  test(`contract drift fails closed: ${label}`, async () => {
    const value = structuredClone(await adoptedContract());
    mutate(value);
    assert.ok(checkD18Contract(value).length > 0);
  });
}

test("fixture manifest is complete, zero-effect and content-bound", async () => {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const digests = { ...manifest.artifacts };
  assert.deepEqual(
    checkD18Bundle(manifest, manifest.artifacts["openapi-v1.json"], digests),
    [],
  );
  const drifted = structuredClone(manifest);
  drifted.effects.tool_calls = 1;
  assert.ok(
    checkD18Bundle(drifted, manifest.artifacts["openapi-v1.json"], digests).length > 0,
  );
});
