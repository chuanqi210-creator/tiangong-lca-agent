#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const REQUIRED_METHODS = {
  "/v1/chat/completions": "post",
  "/v1/jobs": "post",
  "/v1/jobs/{job_id}": "get",
  "/v1/jobs/{job_id}/inputs": "post",
  "/v1/jobs/{job_id}/result": "get",
  "/v1/jobs/{job_id}/events": "get",
  "/v1/jobs/{job_id}/cancel": "post",
  "/v1/jobs/{job_id}/resume": "post",
  "/v1/files/{artifact_id}": "get",
  "/v1/research/{research_id}/material-uploads": "post",
  "/v1/material-uploads/{upload_id}": "get",
  "/v1/material-uploads/{upload_id}/parts/{part_number}": "put",
  "/v1/material-uploads/{upload_id}/complete": "post",
  "/v1/material-uploads/{upload_id}/cancel": "post",
  "/v1/research/{research_id}/materials": "get",
  "/v1/research/{research_id}/materials/{material_id}": "get",
  "/v1/research/{research_id}/material-events": "get",
  "/v1/research/{research_id}/evidence/{locator_id}": "get",
};

const REQUIRED_JOB_STATES = [
  "bounded",
  "cancelled",
  "cancelling",
  "completed",
  "failed",
  "leased",
  "queued",
  "running",
  "waiting_for_input",
];

const REQUIRED_RESULT_FIELDS = [
  "answer",
  "bounded_conclusion",
  "claim_ceiling",
  "limitations",
  "missing_fields",
  "status",
  "summary",
  "values",
  "citations",
  "sourceUse",
  "materialLimitations",
  "route",
  "routeFamily",
  "methods",
  "completedStateChanges",
  "domain_acceptance",
  "officialSources",
  "officialClaimBindings",
  "official_object_ids",
  "source_sha256s",
  "nativeAuthority",
  "scientificBoundary",
];

function sameMembers(actual, expected) {
  return Array.isArray(actual) &&
    JSON.stringify([...actual].sort()) === JSON.stringify([...expected].sort());
}

function refs(schema) {
  return (schema?.anyOf ?? []).map((item) => item?.$ref).filter(Boolean).sort();
}

function contentPartKinds(schema) {
  const variants = schema?.properties?.content?.anyOf ?? [];
  const array = variants.find((item) => item?.type === "array");
  return (array?.items?.oneOf ?? [])
    .map((item) => item?.properties?.type?.const)
    .filter(Boolean)
    .sort();
}

function containsPublicPathSchema(value) {
  if (!value || typeof value !== "object") return false;
  if (value.properties && Object.hasOwn(value.properties, "path")) return true;
  return Object.values(value).some(containsPublicPathSchema);
}

export function checkD18Contract(document) {
  const errors = [];
  if (!document || typeof document !== "object" || Array.isArray(document)) {
    return ["OpenAPI document must be an object"];
  }
  for (const [path, method] of Object.entries(REQUIRED_METHODS)) {
    if (!document.paths?.[path]?.[method]) {
      errors.push(`required operation is absent: ${method.toUpperCase()} ${path}`);
    }
  }
  const schemas = document.components?.schemas ?? {};
  for (const requestName of ["ChatCompletionRequest", "JobRequest"]) {
    if (!schemas[requestName]?.properties?.source_materials) {
      errors.push(`${requestName}.source_materials is absent`);
    }
  }
  if (
    schemas.SourceMaterialInput?.properties?.authority?.const !==
    "user_supplied_project_input"
  ) {
    errors.push("source material authority is not fixed to user input");
  }
  if (!sameMembers(schemas.SourceMaterialInput?.required, [
    "name", "media_type", "sha256", "authority", "content",
  ])) {
    errors.push("source material required fields are incomplete");
  }
  if (schemas.SourceMaterialInput?.properties?.sha256?.pattern !== "^[0-9a-f]{64}$") {
    errors.push("source material sha256 contract drifted");
  }
  if (schemas.SourceMaterialInput?.properties?.content?.$ref !==
      "#/components/schemas/SourceMaterialContent" ||
      !sameMembers(schemas.SourceMaterialContent?.required, ["text"])) {
    errors.push("source material content contract drifted");
  }
  if (!sameMembers(refs(schemas.JobInputRequest?.properties?.payload), [
    "#/components/schemas/DecisionInputPayload",
    "#/components/schemas/MaterialSupplementPayload",
  ]) || !refs(
    schemas.MaterialSupplementPayload?.properties?.source_material,
  ).includes("#/components/schemas/SourceMaterialInput")) {
    errors.push("supplemental material/decision union contract drifted");
  }
  if (!sameMembers(schemas.ContentRefResponse?.required, ["sha256"]) ||
      schemas.ContentRefResponse?.properties?.sha256?.pattern !== "^[0-9a-f]{64}$") {
    errors.push("public content reference contract drifted");
  }
  const states = schemas.JobViewResponse?.properties?.status?.enum;
  if (
    !Array.isArray(states) ||
    JSON.stringify([...states].sort()) !== JSON.stringify(REQUIRED_JOB_STATES)
  ) {
    errors.push("job state contract is incomplete or drifted");
  }
  const inputRequiredFields = Object.keys(
    schemas.InputRequiredResponse?.properties ?? {},
  );
  if (!sameMembers(inputRequiredFields, [
    "interaction_kind",
    "authorization_required",
    "question_contract",
    "question_count",
    "question",
    "missing_fields",
  ])) {
    errors.push("structured waiting question contract is incomplete or drifted");
  }
  const resultFields = Object.keys(
    schemas.ResearchResultResponse?.properties ?? {},
  );
  if (!sameMembers(resultFields, REQUIRED_RESULT_FIELDS)) {
    errors.push("result projection contract is incomplete or drifted");
  }
  const resultResponse = document.paths?.["/v1/jobs/{job_id}/result"]?.get
    ?.responses?.["200"]?.content?.["application/json"]?.schema?.$ref;
  if (resultResponse !== "#/components/schemas/ResultViewResponse") {
    errors.push("job result operation is not bound to ResultViewResponse");
  }
  if (
    !refs(schemas.ResultViewResponse?.properties?.productEvidence).includes(
      "#/components/schemas/ServiceProductEvidenceResponse",
    ) ||
    schemas.ServiceProductEvidenceResponse?.properties?.release_roles?.items?.$ref !==
      "#/components/schemas/ServiceReleaseRoleEvidenceResponse" ||
    !sameMembers(schemas.ServiceProductEvidenceResponse?.required, [
      "authentication",
      "contract",
      "execution_receipt_sha256",
      "job_id",
      "materials",
      "official_sources",
      "project_record",
      "release_identity",
      "release_manifest",
      "release_roles",
      "research_id",
      "result_sha256",
    ])
  ) {
    errors.push("service-authenticated Product Readiness evidence drifted");
  }
  if (
    !refs(schemas.JobCreatedResponse?.properties?.idempotencyReplayEvidence).includes(
      "#/components/schemas/ServiceIdempotencyReplayEvidenceResponse",
    ) ||
    !sameMembers(schemas.ServiceIdempotencyReplayEvidenceResponse?.required, [
      "authentication",
      "contract",
      "idempotency_key_sha256",
      "job_id",
      "release_identity",
      "request_ref_sha256",
      "request_sha256",
      "research_id",
    ])
  ) {
    errors.push("service-authenticated idempotency replay evidence drifted");
  }
  if (containsPublicPathSchema(schemas)) {
    errors.push("public schema exposes a raw path");
  }
  if (schemas.ResultViewResponse?.properties?.attachments?.items?.$ref !==
      "#/components/schemas/PublicAttachmentResponse" ||
      !sameMembers(schemas.PublicAttachmentResponse?.required, [
        "id", "url", "fileUrl", "fileName", "fileType", "mimeType",
      ]) || !schemas.PublicAttachmentResponse?.properties?.fileUrl) {
    errors.push("registered result attachment contract drifted");
  }
  const jobResponse = document.paths?.["/v1/jobs/{job_id}"]?.get
    ?.responses?.["200"]?.content?.["application/json"]?.schema?.$ref;
  if (jobResponse !== "#/components/schemas/JobViewResponse") {
    errors.push("job operation is not bound to JobViewResponse");
  }
  if (!sameMembers(contentPartKinds(schemas.ChatMessage), [
    "file", "image_url", "input_audio", "text",
  ])) {
    errors.push("Qingxiaoda content-part schema is incomplete");
  }
  const filePart = (schemas.ChatMessage?.properties?.content?.anyOf ?? [])
    .find((item) => item?.type === "array")?.items?.oneOf
    ?.find((item) => item?.properties?.type?.const === "file")
    ?.properties?.file;
  if (!sameMembers(Object.keys(filePart?.properties ?? {}), [
    "file_id", "filename", "url",
  ]) || !sameMembers(filePart?.required, ["filename"])) {
    errors.push("Qingxiaoda file URL/file_id contract drifted");
  }
  const partOperation = document.paths?.[
    "/v1/material-uploads/{upload_id}/parts/{part_number}"
  ]?.put;
  const headerNames = (partOperation?.parameters ?? [])
    .filter((item) => item?.in === "header")
    .map((item) => item.name)
    .sort();
  if (!sameMembers(headerNames, [
    "Content-Range", "X-D18-Project-ID", "X-Part-SHA256",
  ])) {
    errors.push("resumable upload header contract drifted");
  }
  if (
    schemas.MaterialSummaryResponse?.properties?.transfer_status?.type !== "string" ||
    schemas.MaterialRevisionResponse?.properties?.processing_status?.type !== "string" ||
    schemas.MaterialRevisionResponse?.properties?.scientific_status?.type !== "string"
  ) {
    errors.push("material state-axis schemas are incomplete");
  }
  if (!sameMembers(schemas.ProblemDetail?.required, ["code", "detail"]) ||
      schemas.ProblemDetail?.properties?.code?.type !== "string" ||
      schemas.ProblemDetail?.properties?.detail?.type !== "string") {
    errors.push("material problem-code schema is absent");
  }
  const eventSchema = document.paths?.[
    "/v1/research/{research_id}/material-events"
  ]?.get?.responses?.["200"]?.content?.["text/event-stream"]?.schema;
  if (eventSchema?.["x-d18-event-contract"] !==
      "lca_research_service.material_event/v1") {
    errors.push("material event-stream shape is absent");
  }
  if (!sameMembers(schemas.PublicCitationResponse?.required, [
    "citationId", "displayName", "evidenceUrl", "locator", "materialId",
    "processingStatus", "revisionId", "sourceAuthority",
  ])) {
    errors.push("citation projection schema drifted");
  }
  if (
    schemas.ResearchResultResponse?.properties?.officialSources?.items?.$ref !==
      "#/components/schemas/PublicOfficialSourceResponse" ||
    schemas.PublicOfficialSourceResponse?.properties?.authority?.const !==
      "official_public_source" ||
    !sameMembers(schemas.PublicOfficialSourceResponse?.required, [
      "accessedAt", "authority", "bodySha256", "finalUrl", "parseLocation",
      "receiptSha256", "requestedUrl", "snapshotVersion", "sourceAuthority",
      "sourceId", "sourceVersion",
    ])
  ) {
    errors.push("official source projection schema drifted");
  }
  if (
    schemas.ResearchResultResponse?.properties?.officialClaimBindings?.items?.$ref !==
      "#/components/schemas/PublicOfficialClaimBindingResponse" ||
    !sameMembers(schemas.PublicOfficialClaimBindingResponse?.required, [
      "claimId", "modality", "sourceIds", "sourceSha256s", "statement",
    ])
  ) {
    errors.push("official claim-source projection schema drifted");
  }
  return errors.sort();
}

export function checkD18Bundle(manifest, openapiDigest, artifactDigests) {
  const errors = [];
  if (manifest?.contract !== "lca_research_service.integration_bundle/v1") {
    errors.push("integration fixture manifest contract drifted");
  }
  if (!sameMembers(Object.keys(manifest?.fixtures ?? {}), [
    "bounded", "cancelled", "failed", "resumed", "success", "waiting",
  ])) {
    errors.push("integration fixture scenarios are incomplete");
  }
  if (JSON.stringify(manifest?.effects) !== JSON.stringify({
    external_writes: 0,
    model_calls: 0,
    scientific_results: 0,
    tool_calls: 0,
  })) {
    errors.push("integration fixture effects are not exactly zero");
  }
  if (manifest?.artifacts?.["openapi-v1.json"] !== openapiDigest) {
    errors.push("integration OpenAPI digest differs from manifest");
  }
  for (const [path, digest] of Object.entries(manifest?.artifacts ?? {})) {
    if (artifactDigests[path] !== digest) {
      errors.push(`integration artifact digest drifted: ${path}`);
    }
  }
  return errors.sort();
}

async function main(argv) {
  const position = argv.indexOf("--openapi");
  if (position < 0 || !argv[position + 1]) {
    throw new Error("usage: check-d18-contract.mjs --openapi PATH");
  }
  const payload = await readFile(argv[position + 1]);
  const digest = createHash("sha256").update(payload).digest("hex");
  let value;
  try {
    value = JSON.parse(payload.toString("utf8"));
  } catch {
    value = null;
  }
  const errors = checkD18Contract(value);
  const manifestPosition = argv.indexOf("--manifest");
  if (manifestPosition < 0 || !argv[manifestPosition + 1]) {
    errors.push("integration manifest is required");
  } else {
    const manifestPath = pathToFileURL(argv[manifestPosition + 1]);
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    const root = new URL("./", manifestPath);
    const digests = {};
    for (const path of Object.keys(manifest.artifacts ?? {})) {
      const artifact = await readFile(new URL(path, root));
      digests[path] = createHash("sha256").update(artifact).digest("hex");
    }
    errors.push(...checkD18Bundle(manifest, digest, digests));
  }
  process.stdout.write(
    `${JSON.stringify({
      contract: "lca_research_agent.frontend_contract_check/v1",
      status: errors.length === 0 ? "passed" : "failed",
      errors,
      openapi_sha256: digest,
    })}\n`,
  );
  return errors.length === 0 ? 0 : 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main(process.argv.slice(2));
}
