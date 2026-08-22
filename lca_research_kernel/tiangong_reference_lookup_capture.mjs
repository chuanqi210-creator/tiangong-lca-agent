#!/usr/bin/env node

import {
  chmodSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  writeFileSync,
} from "node:fs";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";


const CLI_PACKAGE = "@tiangong-lca/cli";
const CLI_VERSION = "0.0.29";
const CLI_NPM_INTEGRITY =
  "sha512-iee5B/rzTTZxI7XWxYZnyzcEp1r8jTKw6KBn8EcwEatGpzv1xw8XJdFKv+JQ15GFeV0uJSz5AJ/AGf5X+fQ6hg==";
const CLI_PACKAGE_LOCK_SHA256 =
  "d8a014be7b26295fe04b6b52102c5901c9543ed2df98a64bbdeae3adb7f8abdf";
const CLI_NODE_MODULES_TREE_SHA256 =
  "9b4e599b73a2eb9186e8d1ba3b37ab32d75c8b254b2908cd8004b1d3eb72f188";
const QUALIFICATION_CONTRACT =
  "lca_research_kernel.research_council_benchmark_qualification/v2";
const ADOPTION_CONTRACT =
  "lca_research_kernel.tiangong_capability_adoption/v1";
const ADOPTION_CONTENT_SHA256 =
  "4069fd724039d86cf0a8d9bd95e236a9da3c9b1f7903cf7959c5f5ffcada51a7";
const RPC_NAME = "search_dataset_json_uuid_mentions";
const FRONTEND_CONTRACT =
  "lca_research_kernel.tiangong_reference_lookup_frontend/v2";
const CAPTURE_CONTRACT =
  "lca_research_kernel.tiangong_reference_lookup_capture/v2";
const OFFICIAL_HOST = "lca.tiangong.earth";
const WORKSPACE_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const ZERO_EFFECTS = {
  remote_write_executed_count: 0,
  shared_write_executed_count: 0,
  publication_executed_count: 0,
  review_submission_executed_count: 0,
  contact_executed_count: 0,
  payment_executed_count: 0,
  role_administration_executed_count: 0,
  external_message_executed_count: 0,
  unofficial_tiangong_correction_count: 0,
  unsupported_strong_public_claim_count: 0,
  secret_persistence_count: 0,
};


function parseArgs(argv) {
  const allowed = new Set([
    "--qualification",
    "--frontend-main-url",
    "--frontend-chunk-url",
    "--adoption",
    "--cli-root",
    "--output-dir",
  ]);
  if (argv.length % 2 !== 0) {
    throw new Error("ARGUMENT_SHAPE_INVALID");
  }
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!allowed.has(key) || !value || result[key]) {
      throw new Error("ARGUMENT_INVALID");
    }
    result[key] = value;
  }
  for (const key of allowed) {
    if (!result[key]) {
      throw new Error("ARGUMENT_REQUIRED");
    }
  }
  return result;
}


function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}


function canonicalJson(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error("CANONICAL_JSON_INVALID");
    }
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    return `{${keys
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  throw new Error("CANONICAL_JSON_INVALID");
}


function contentIdentity(value, digestField, idField, errorCode) {
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    typeof value[digestField] !== "string" ||
    !/^[0-9a-f]{64}$/u.test(value[digestField]) ||
    value[idField] !== value[digestField].slice(0, 16)
  ) {
    throw new Error(errorCode);
  }
  const body = { ...value };
  delete body[digestField];
  delete body[idField];
  if (sha256(Buffer.from(canonicalJson(body), "utf8")) !== value[digestField]) {
    throw new Error(errorCode);
  }
}


function validateQualificationWithKernel(qualificationPath) {
  const completed = spawnSync(
    "python3",
    [
      "-m",
      "lca_research_kernel.research_council_provider_closure_audit",
      "--qualification",
      qualificationPath,
      "--validate-qualification-only",
    ],
    {
      cwd: WORKSPACE_ROOT,
      encoding: "utf8",
      env: {
        LANG: process.env.LANG ?? "C.UTF-8",
        PATH: process.env.PATH ?? "/usr/bin:/bin",
        PYTHONNOUSERSITE: "1",
        PYTHONPATH: WORKSPACE_ROOT,
      },
      maxBuffer: 1024 * 1024,
    },
  );
  if (completed.status !== 0) {
    throw new Error("QUALIFICATION_IDENTITY_INVALID");
  }
  try {
    const summary = JSON.parse(completed.stdout);
    if (
      typeof summary.qualification_id !== "string" ||
      typeof summary.qualification_content_sha256 !== "string" ||
      !Number.isInteger(summary.candidate_count) ||
      summary.candidate_count <= 0
    ) {
      throw new Error("QUALIFICATION_IDENTITY_INVALID");
    }
    return summary;
  } catch {
    throw new Error("QUALIFICATION_IDENTITY_INVALID");
  }
}


function validateQualification(value, validation) {
  if (value?.contract !== QUALIFICATION_CONTRACT) {
    throw new Error("QUALIFICATION_CONTRACT_INVALID");
  }
  if (
    value.qualification_id !== validation.qualification_id ||
    value.qualification_content_sha256 !==
      validation.qualification_content_sha256 ||
    !Array.isArray(value.candidate_ledger) ||
    value.candidate_ledger.length !== validation.candidate_count
  ) {
    throw new Error("QUALIFICATION_CANDIDATES_INVALID");
  }
  const identities = new Set();
  for (const candidate of value.candidate_ledger) {
    if (
      typeof candidate !== "object" ||
      candidate === null ||
      Array.isArray(candidate) ||
      typeof candidate.cluster_id !== "string" ||
      !candidate.cluster_id.trim() ||
      typeof candidate.process_id !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u.test(
        candidate.process_id,
      ) ||
      typeof candidate.process_version !== "string" ||
      !/^[0-9]+(?:\.[0-9]+)+$/u.test(candidate.process_version)
    ) {
      throw new Error("QUALIFICATION_CANDIDATES_INVALID");
    }
    const identity = `${candidate.process_id}@${candidate.process_version}`;
    if (identities.has(identity)) {
      throw new Error("QUALIFICATION_CANDIDATE_DUPLICATE");
    }
    identities.add(identity);
  }
  return value;
}


function validateAdoption(value) {
  if (value?.contract !== ADOPTION_CONTRACT) {
    throw new Error("ADOPTION_CONTRACT_INVALID");
  }
  contentIdentity(
    value,
    "adoption_content_sha256",
    "adoption_id",
    "ADOPTION_IDENTITY_INVALID",
  );
  if (
    value.adoption_content_sha256 !== ADOPTION_CONTENT_SHA256 ||
    value.current_package_spec !== `${CLI_PACKAGE}@${CLI_VERSION}` ||
    value.current_version !== CLI_VERSION ||
    value.npm_integrity !== CLI_NPM_INTEGRITY ||
    value.status !== "adopted_read_only_surface" ||
    value.remote_write_authorized !== false
  ) {
    throw new Error("ADOPTION_BINDING_INVALID");
  }
  return value;
}


function hashDirectoryTree(root) {
  const rows = [];
  function walk(directory) {
    for (const name of readdirSync(directory).sort()) {
      const absolute = path.join(directory, name);
      const relative = path.relative(root, absolute).split(path.sep).join("/");
      const status = lstatSync(absolute);
      if (status.isDirectory()) {
        walk(absolute);
      } else if (status.isSymbolicLink()) {
        rows.push({ path: relative, symlink: readlinkSync(absolute) });
      } else if (status.isFile()) {
        const bytes = readFileSync(absolute);
        rows.push({ path: relative, bytes: bytes.length, sha256: sha256(bytes) });
      } else {
        throw new Error("CLI_RUNTIME_TREE_INVALID");
      }
    }
  }
  walk(root);
  return sha256(Buffer.from(JSON.stringify(rows), "utf8"));
}


function verifyCliRuntime(cliRoot, adoption) {
  const nodeModulesRoot = path.resolve(cliRoot, "../..");
  const npxRoot = path.dirname(nodeModulesRoot);
  const lockBytes = readFileSync(path.join(npxRoot, "package-lock.json"));
  if (sha256(lockBytes) !== CLI_PACKAGE_LOCK_SHA256) {
    throw new Error("CLI_PACKAGE_LOCK_MISMATCH");
  }
  if (hashDirectoryTree(nodeModulesRoot) !== CLI_NODE_MODULES_TREE_SHA256) {
    throw new Error("CLI_RUNTIME_TREE_MISMATCH");
  }
  const packageMetadata = JSON.parse(
    readFileSync(path.join(cliRoot, "package.json"), "utf8"),
  );
  const lock = JSON.parse(lockBytes.toString("utf8"));
  const lockedPackage = lock?.packages?.["node_modules/@tiangong-lca/cli"];
  if (
    packageMetadata.name !== CLI_PACKAGE ||
    packageMetadata.version !== CLI_VERSION ||
    lockedPackage?.version !== CLI_VERSION ||
    lockedPackage?.integrity !== adoption.npm_integrity ||
    lockedPackage?.resolved !== adoption.tarball_url
  ) {
    throw new Error("PINNED_CLI_RUNTIME_MISMATCH");
  }
  return {
    package_lock_sha256: CLI_PACKAGE_LOCK_SHA256,
    node_modules_tree_sha256: CLI_NODE_MODULES_TREE_SHA256,
  };
}


function officialUrl(value, expectedPrefix) {
  const url = new URL(value);
  if (
    url.protocol !== "https:" ||
    url.hostname !== OFFICIAL_HOST ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    !url.pathname.startsWith(expectedPrefix)
  ) {
    throw new Error("OFFICIAL_FRONTEND_URL_INVALID");
  }
  return url;
}


async function fetchSource(url) {
  const response = await fetch(url, {
    method: "GET",
    headers: { Accept: "application/javascript,text/javascript,*/*" },
    redirect: "error",
  });
  if (!response.ok) {
    throw new Error("OFFICIAL_FRONTEND_READ_FAILED");
  }
  return Buffer.from(await response.arrayBuffer());
}


function uniqueMatch(text, pattern, code) {
  const matches = [...new Set(text.match(pattern) ?? [])];
  if (matches.length !== 1) {
    throw new Error(code);
  }
  return matches[0];
}


function moduleSource(text, moduleId) {
  const marker = `${moduleId}:function`;
  const start = text.indexOf(marker);
  if (start < 0) {
    throw new Error("FRONTEND_REFERENCE_LOOKUP_CONTRACT_MISSING");
  }
  const remainder = text.slice(start + marker.length);
  const next = remainder.search(/\},\d+:function/u);
  return text.slice(start, next < 0 ? text.length : start + marker.length + next);
}


function verifyFrontendReferenceLookup(mainText, chunkText) {
  const sharedModule = moduleSource(mainText, "57171");
  const apiModule = moduleSource(mainText, "83736");
  const pageModule = moduleSource(chunkText, "74626");
  if (
    !sharedModule.includes(RPC_NAME) ||
    !sharedModule.includes("p_data_source:Q") ||
    !sharedModule.includes("p_limit:ae") ||
    !sharedModule.includes("p_source_entity_kinds:X") ||
    !sharedModule.includes("p_state_code_filter:S(te)") ||
    !sharedModule.includes("p_team_id_filter:W") ||
    !sharedModule.includes("p_this_user_id:") ||
    !sharedModule.includes("p_uuid:D") ||
    !sharedModule.includes("i=50") ||
    !apiModule.includes("g=t(57171)") ||
    !apiModule.includes("(0,g.xS)(") ||
    !apiModule.includes('sourceEntityKinds:["lifecyclemodel"]') ||
    !pageModule.includes("D=a(83736)") ||
    !pageModule.includes("(0,D.eh)(") ||
    !pageModule.includes("pages.search.referenceLookup")
  ) {
    throw new Error("FRONTEND_REFERENCE_LOOKUP_CONTRACT_MISSING");
  }
}


function outputDirectory(value) {
  const resolved = path.resolve(value);
  const outputsRoot = path.resolve(WORKSPACE_ROOT, "outputs");
  const relative = path.relative(outputsRoot, resolved);
  if (
    !relative ||
    path.isAbsolute(relative) ||
    relative === ".." ||
    relative.startsWith(`..${path.sep}`)
  ) {
    throw new Error("OUTPUT_DIRECTORY_INVALID");
  }
  if (existsSync(resolved)) {
    throw new Error("FRONTEND_OUTPUT_ALREADY_EXISTS");
  }
  return resolved;
}


function writeOutputDirectoryNoClobber(
  outputDir,
  mainUrl,
  chunkUrl,
  mainBytes,
  chunkBytes,
  frontendManifest,
  capture,
) {
  if (existsSync(outputDir)) {
    throw new Error("FRONTEND_OUTPUT_ALREADY_EXISTS");
  }
  mkdirSync(path.dirname(outputDir), { recursive: true, mode: 0o700 });
  try {
    mkdirSync(outputDir, { recursive: false, mode: 0o700 });
  } catch (error) {
    if (error?.code === "EEXIST") {
      throw new Error("FRONTEND_OUTPUT_ALREADY_EXISTS");
    }
    throw error;
  }
  const frontendDir = path.join(outputDir, "frontend");
  mkdirSync(frontendDir, { recursive: false, mode: 0o700 });
  const files = [
    [path.join("frontend", path.basename(mainUrl.pathname)), mainBytes],
    [path.join("frontend", path.basename(chunkUrl.pathname)), chunkBytes],
    [
      "frontend-manifest.json",
      Buffer.from(`${JSON.stringify(frontendManifest, null, 2)}\n`, "utf8"),
    ],
    [
      "reference-lookup-capture.json",
      Buffer.from(`${JSON.stringify(capture, null, 2)}\n`, "utf8"),
    ],
  ];
  for (const [relative, bytes] of files) {
    const target = path.join(outputDir, relative);
    writeFileSync(target, bytes, { flag: "wx", mode: 0o600 });
    chmodSync(target, 0o600);
  }
}


function sanitizeErrorCode(value) {
  if (typeof value !== "string" || !/^[A-Z0-9_]{1,80}$/u.test(value)) {
    return "REMOTE_READ_FAILED";
  }
  return value;
}


function normalizeTimestamp(value) {
  if (typeof value !== "string" || !value.trim()) {
    return null;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}


function sessionSubject(accessToken) {
  const parts = accessToken.split(".");
  if (parts.length !== 3) {
    throw new Error("AUTHENTICATED_ACTOR_MISSING");
  }
  const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
  if (typeof payload.sub !== "string" || !payload.sub) {
    throw new Error("AUTHENTICATED_ACTOR_MISSING");
  }
  return payload.sub;
}


async function run() {
  const args = parseArgs(process.argv.slice(2));
  const qualificationPath = path.resolve(args["--qualification"]);
  const qualificationValidation =
    validateQualificationWithKernel(qualificationPath);
  const qualification = validateQualification(
    JSON.parse(readFileSync(qualificationPath, "utf8")),
    qualificationValidation,
  );
  const adoption = validateAdoption(
    JSON.parse(readFileSync(path.resolve(args["--adoption"]), "utf8")),
  );
  const cliRoot = path.resolve(args["--cli-root"]);
  const cliRuntimeEvidence = verifyCliRuntime(cliRoot, adoption);
  const outputDir = outputDirectory(args["--output-dir"]);

  const mainUrl = officialUrl(args["--frontend-main-url"], "/umi.");
  const chunkUrl = officialUrl(
    args["--frontend-chunk-url"],
    "/p__LifeCycleModels__index.",
  );
  const [mainBytes, chunkBytes] = await Promise.all([
    fetchSource(mainUrl),
    fetchSource(chunkUrl),
  ]);
  const mainText = mainBytes.toString("utf8");
  const chunkText = chunkBytes.toString("utf8");
  verifyFrontendReferenceLookup(mainText, chunkText);

  const publicBaseUrl = uniqueMatch(
    mainText,
    /https:\/\/[a-z0-9]+\.supabase\.co/gu,
    "PUBLIC_BASE_URL_AMBIGUOUS",
  );
  const publicPublishableValue = uniqueMatch(
    mainText,
    /sb_publishable_[A-Za-z0-9_-]+/gu,
    "PUBLIC_RUNTIME_VALUE_AMBIGUOUS",
  );
  process.env.TIANGONG_LCA_API_BASE_URL = publicBaseUrl;
  process.env.TIANGONG_LCA_SUPABASE_PUBLISHABLE_KEY = publicPublishableValue;
  process.env.TIANGONG_LCA_DISABLE_SESSION_CACHE = "true";
  if (!process.env.TIANGONG_LCA_API_KEY) {
    throw new Error("AUTHENTICATED_RUNTIME_MISSING");
  }

  const clientModule = await import(
    pathToFileURL(path.join(cliRoot, "dist/src/lib/supabase-client.js")).href
  );
  const sessionModule = await import(
    pathToFileURL(path.join(cliRoot, "dist/src/lib/supabase-session.js")).href
  );
  const runtime = clientModule.requireSupabaseRestRuntime(process.env);
  const authenticatedRuntime = sessionModule.createSupabaseDataRuntime({
    runtime,
    fetchImpl: fetch,
    timeoutMs: 30_000,
  });
  const accessValue = await authenticatedRuntime.getAccessToken();
  const actorId = sessionSubject(accessValue);
  const { client } = clientModule.createSupabaseDataClient(
    authenticatedRuntime,
    fetch,
    30_000,
  );

  const candidates = qualification.candidate_ledger.map((candidate) => ({
    cluster_id: candidate.cluster_id,
    process_id: candidate.process_id,
    process_version: candidate.process_version,
  }));
  const identities = candidates.map(
    (candidate) => `${candidate.process_id}@${candidate.process_version}`,
  );
  if (new Set(identities).size !== identities.length) {
    throw new Error("QUALIFICATION_CANDIDATE_DUPLICATE");
  }

  const candidateResults = [];
  for (let offset = 0; offset < candidates.length; offset += 6) {
    const batch = candidates.slice(offset, offset + 6);
    const batchResults = await Promise.all(
      batch.map(async (candidate) => {
        const response = await client.rpc(RPC_NAME, {
          p_data_source: "tg",
          p_limit: 50,
          p_source_entity_kinds: ["lifecyclemodel"],
          p_state_code_filter: null,
          p_team_id_filter: null,
          p_this_user_id: actorId,
          p_uuid: candidate.process_id,
        });
        if (!response.error && !Array.isArray(response.data)) {
          throw new Error("RPC_RESULT_SHAPE_INVALID");
        }
        return {
          ...candidate,
          http_status: response.status,
          success: !response.error,
          error_code: response.error
            ? sanitizeErrorCode(response.error.code)
            : null,
          error_message: response.error ? "remote_read_failed" : null,
          mentions: response.error
            ? []
            : response.data.map((row) => ({
                source_id: row.source_id ?? null,
                source_version: row.source_version ?? null,
                source_entity_kind: row.source_entity_kind ?? null,
                source_state_code: row.source_state_code ?? null,
                source_modified_at: normalizeTimestamp(row.source_modified_at),
              })),
        };
      }),
    );
    candidateResults.push(...batchResults);
  }

  const observedAt = new Date().toISOString();
  const mainArtifactPath = path
    .relative(
      WORKSPACE_ROOT,
      path.join(outputDir, "frontend", path.basename(mainUrl.pathname)),
    )
    .split(path.sep)
    .join("/");
  const chunkArtifactPath = path
    .relative(
      WORKSPACE_ROOT,
      path.join(outputDir, "frontend", path.basename(chunkUrl.pathname)),
    )
    .split(path.sep)
    .join("/");
  const frontendManifest = {
    contract: FRONTEND_CONTRACT,
    retrieved_at_utc: observedAt,
    ui_root_url: "https://lca.tiangong.earth/",
    main_bundle: {
      url: mainUrl.toString(),
      sha256: sha256(mainBytes),
      bytes: mainBytes.length,
      artifact_path: mainArtifactPath,
    },
    lifecyclemodel_chunk: {
      url: chunkUrl.toString(),
      sha256: sha256(chunkBytes),
      bytes: chunkBytes.length,
      artifact_path: chunkArtifactPath,
    },
    implementation: {
      page_module_id: "74626",
      api_module_id: "83736",
      shared_module_id: "57171",
      rpc_name: RPC_NAME,
      data_source: "tg",
      source_entity_kinds: ["lifecyclemodel"],
      maximum_results: 50,
    },
  };
  const capture = {
    contract: CAPTURE_CONTRACT,
    observed_at_utc: observedAt,
    qualification_id: qualification.qualification_id,
    qualification_content_sha256: qualification.qualification_content_sha256,
    frontend_main_bundle_sha256: frontendManifest.main_bundle.sha256,
    frontend_lifecyclemodel_chunk_sha256:
      frontendManifest.lifecyclemodel_chunk.sha256,
    runtime: {
      cli_package: CLI_PACKAGE,
      cli_version: CLI_VERSION,
      adoption_id: adoption.adoption_id,
      adoption_content_sha256: adoption.adoption_content_sha256,
      npm_integrity: adoption.npm_integrity,
      package_lock_sha256: cliRuntimeEvidence.package_lock_sha256,
      node_modules_tree_sha256: cliRuntimeEvidence.node_modules_tree_sha256,
      capture_tool_sha256: sha256(readFileSync(fileURLToPath(import.meta.url))),
      session_cache_disabled: true,
      authenticated_runtime_present: true,
      authenticated_actor_present: true,
      public_runtime_present: true,
    },
    request: {
      rpc_name: RPC_NAME,
      data_source: "tg",
      limit: 50,
      source_entity_kinds: ["lifecyclemodel"],
      state_code_filter: null,
      team_id_filter: null,
      authenticated_actor_binding: "session_subject",
    },
    candidate_results: candidateResults,
    effects: ZERO_EFFECTS,
  };
  writeOutputDirectoryNoClobber(
    outputDir,
    mainUrl,
    chunkUrl,
    mainBytes,
    chunkBytes,
    frontendManifest,
    capture,
  );

  const failed = candidateResults.filter((row) => !row.success);
  const hits = candidateResults.filter((row) => row.mentions.length > 0);
  process.stdout.write(
    `${JSON.stringify({
      qualification_id: qualification.qualification_id,
      candidate_count: candidateResults.length,
      successful_candidate_count: candidateResults.length - failed.length,
      failed_candidate_count: failed.length,
      mention_candidate_count: hits.length,
      mention_row_count: hits.reduce(
        (total, row) => total + row.mentions.length,
        0,
      ),
    })}\n`,
  );
}


run().catch((error) => {
  const code =
    error instanceof Error && /^[A-Z0-9_]+$/u.test(error.message)
      ? error.message
      : "REFERENCE_LOOKUP_CAPTURE_FAILED";
  process.stderr.write(`${code}\n`);
  process.exitCode = 1;
});
