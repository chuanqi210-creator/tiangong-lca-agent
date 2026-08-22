#!/usr/bin/env node

import {
  chmodSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  realpathSync,
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
const ADOPTION_CONTRACT =
  "lca_research_kernel.tiangong_capability_adoption/v1";
const ADOPTION_CONTENT_SHA256 =
  "4069fd724039d86cf0a8d9bd95e236a9da3c9b1f7903cf7959c5f5ffcada51a7";
const ADOPTION_FILE_SHA256 =
  "2cf12c9278ca877e0e2eff098070229c4802c69f54527fb42c362cda44dfd592";
const POLICY_CONTRACT =
  "lca_research_kernel.research_council_benchmark_portfolio_policy/v1";
const POLICY_FILE_SHA256 =
  "2d5cd08aa117207ed34d3cc6527a9cc0dc873111bc9d6ce581ffe093cf98c427";
const CAPTURE_CONTRACT =
  "lca_research_kernel.tiangong_lifecyclemodel_surface_capture/v1";
const FRONTEND_CONTRACT =
  "lca_research_kernel.tiangong_lifecyclemodel_frontend/v1";
const RECEIPT_CONTRACT =
  "lca_research_kernel.tiangong_lifecyclemodel_surface_runtime_receipt/v1";
const OFFICIAL_FRONTEND_ROOT = "https://lca.tiangong.earth/";
const MODEL_CAP = 10_000;
const PAGE_SIZE = 100;
const RETRY_LIMIT = 2;
const RUNNER_REPOSITORY_PATH =
  "lca_research_kernel/tiangong_lifecyclemodel_surface_capture.mjs";
const WORKSPACE_ROOT = realpathSync(path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
));
const PUBLIC_MODEL_FIELDS = [
  "id",
  "version",
  "state_code",
  "json",
  "json_ordered",
  "json_tg",
  "rule_verification",
  "modified_at",
];
const PUBLIC_MODEL_SELECT = PUBLIC_MODEL_FIELDS.join(",");
const ZERO_EFFECTS = {
  remote_write_executed_count: 0,
  shared_write_executed_count: 0,
  calculation_trigger_executed_count: 0,
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


function fail(code, partial = null) {
  const error = new Error(code);
  if (partial !== null) {
    error.partial = partial;
  }
  throw error;
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
      fail("CANONICAL_JSON_INVALID");
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
  fail("CANONICAL_JSON_INVALID");
}


function withContentIdentity(body, digestField, idField) {
  const digest = sha256(Buffer.from(canonicalJson(body), "utf8"));
  return { ...body, [digestField]: digest, [idField]: digest.slice(0, 16) };
}


function validateContentIdentity(value, digestField, idField, errorCode) {
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    typeof value[digestField] !== "string" ||
    !/^[0-9a-f]{64}$/u.test(value[digestField]) ||
    value[idField] !== value[digestField].slice(0, 16)
  ) {
    fail(errorCode);
  }
  const body = { ...value };
  delete body[digestField];
  delete body[idField];
  if (sha256(Buffer.from(canonicalJson(body), "utf8")) !== value[digestField]) {
    fail(errorCode);
  }
}


function parseArgs(argv) {
  const allowed = new Set([
    "--adoption",
    "--cli-root",
    "--frontend-root",
    "--policy",
    "--output-dir",
  ]);
  if (argv.length !== allowed.size * 2) {
    fail("ARGUMENT_SHAPE_INVALID");
  }
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!allowed.has(key) || !value || result[key]) {
      fail("ARGUMENT_INVALID");
    }
    result[key] = value;
  }
  if ([...allowed].some((key) => !result[key])) {
    fail("ARGUMENT_REQUIRED");
  }
  return result;
}


function outputDirectory(value) {
  const outputsRoot = path.resolve(WORKSPACE_ROOT, "outputs");
  return validateOutputPath(value, outputsRoot);
}


function lstatNoFollow(value) {
  try {
    return lstatSync(value);
  } catch (error) {
    if (error?.code === "ENOENT") {
      return null;
    }
    fail("OUTPUT_DIRECTORY_ANCESTOR_INVALID");
  }
}


export function validateOutputPath(value, outputsRoot) {
  const resolved = path.resolve(value);
  const resolvedOutputsRoot = path.resolve(outputsRoot);
  const relative = path.relative(resolvedOutputsRoot, resolved);
  if (
    !relative ||
    path.isAbsolute(relative) ||
    relative === ".." ||
    relative.startsWith(`..${path.sep}`)
  ) {
    fail("OUTPUT_DIRECTORY_INVALID");
  }
  const rootStatus = lstatNoFollow(resolvedOutputsRoot);
  if (rootStatus?.isSymbolicLink()) {
    fail("OUTPUT_DIRECTORY_SYMLINK_INVALID");
  }
  if (rootStatus === null || !rootStatus.isDirectory()) {
    fail("OUTPUT_DIRECTORY_ANCESTOR_INVALID");
  }
  const parts = relative.split(path.sep);
  let cursor = resolvedOutputsRoot;
  for (let index = 0; index < parts.length; index += 1) {
    const part = parts[index];
    cursor = path.join(cursor, part);
    const status = lstatNoFollow(cursor);
    if (status === null) {
      return resolved;
    }
    if (status.isSymbolicLink()) {
      fail("OUTPUT_DIRECTORY_SYMLINK_INVALID");
    }
    if (index === parts.length - 1) {
      fail("SURFACE_CAPTURE_ALREADY_EXISTS");
    }
    if (!status.isDirectory()) {
      fail("OUTPUT_DIRECTORY_ANCESTOR_INVALID");
    }
  }
  return resolved;
}


function readPinnedJson(filePath, fileDigest, readCode, identityCode) {
  let bytes;
  try {
    bytes = readFileSync(path.resolve(filePath));
  } catch {
    fail(readCode);
  }
  if (sha256(bytes) !== fileDigest) {
    fail(identityCode);
  }
  try {
    return { bytes, value: JSON.parse(bytes.toString("utf8")) };
  } catch {
    fail(readCode);
  }
}


function validatePolicy(filePath) {
  const { value } = readPinnedJson(
    filePath,
    POLICY_FILE_SHA256,
    "POLICY_READ_FAILED",
    "POLICY_IDENTITY_INVALID",
  );
  if (
    value?.contract !== POLICY_CONTRACT ||
    value.discovery_model_cap !== MODEL_CAP ||
    value.remote_write_allowed !== false
  ) {
    fail("POLICY_BINDING_INVALID");
  }
  return value;
}


function validateAdoption(filePath) {
  const { value } = readPinnedJson(
    filePath,
    ADOPTION_FILE_SHA256,
    "ADOPTION_READ_FAILED",
    "ADOPTION_FILE_IDENTITY_INVALID",
  );
  if (value?.contract !== ADOPTION_CONTRACT) {
    fail("ADOPTION_CONTRACT_INVALID");
  }
  validateContentIdentity(
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
    fail("ADOPTION_BINDING_INVALID");
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
        fail("CLI_RUNTIME_TREE_INVALID");
      }
    }
  }
  try {
    walk(root);
  } catch (error) {
    if (error instanceof Error && /^[A-Z0-9_]+$/u.test(error.message)) {
      throw error;
    }
    fail("CLI_RUNTIME_TREE_INVALID");
  }
  return sha256(Buffer.from(JSON.stringify(rows), "utf8"));
}


function verifyCliRuntime(cliRoot, adoption) {
  const resolvedCliRoot = path.resolve(cliRoot);
  const nodeModulesRoot = path.resolve(resolvedCliRoot, "../..");
  const npxRoot = path.dirname(nodeModulesRoot);
  let lockBytes;
  try {
    lockBytes = readFileSync(path.join(npxRoot, "package-lock.json"));
  } catch {
    fail("CLI_PACKAGE_LOCK_READ_FAILED");
  }
  if (sha256(lockBytes) !== CLI_PACKAGE_LOCK_SHA256) {
    fail("CLI_PACKAGE_LOCK_MISMATCH");
  }
  if (hashDirectoryTree(nodeModulesRoot) !== CLI_NODE_MODULES_TREE_SHA256) {
    fail("CLI_RUNTIME_TREE_MISMATCH");
  }
  let packageMetadata;
  let lock;
  try {
    packageMetadata = JSON.parse(
      readFileSync(path.join(resolvedCliRoot, "package.json"), "utf8"),
    );
    lock = JSON.parse(lockBytes.toString("utf8"));
  } catch {
    fail("PINNED_CLI_RUNTIME_INVALID");
  }
  const lockedPackage = lock?.packages?.["node_modules/@tiangong-lca/cli"];
  if (
    packageMetadata.name !== CLI_PACKAGE ||
    packageMetadata.version !== CLI_VERSION ||
    lockedPackage?.version !== CLI_VERSION ||
    lockedPackage?.integrity !== adoption.npm_integrity ||
    lockedPackage?.resolved !== adoption.tarball_url
  ) {
    fail("PINNED_CLI_RUNTIME_MISMATCH");
  }
  return {
    cliRoot: resolvedCliRoot,
    packageLockSha256: CLI_PACKAGE_LOCK_SHA256,
    nodeModulesTreeSha256: CLI_NODE_MODULES_TREE_SHA256,
  };
}


function assertVersionedRunnerBytes() {
  const runnerBytes = readFileSync(fileURLToPath(import.meta.url));
  const versioned = spawnSync(
    "git",
    ["show", `HEAD:${RUNNER_REPOSITORY_PATH}`],
    {
      cwd: WORKSPACE_ROOT,
      encoding: null,
      env: {
        LANG: process.env.LANG ?? "C.UTF-8",
        PATH: process.env.PATH ?? "/usr/bin:/bin",
      },
      maxBuffer: 16 * 1024 * 1024,
    },
  );
  if (
    versioned.status !== 0 ||
    !Buffer.isBuffer(versioned.stdout) ||
    !runnerBytes.equals(versioned.stdout)
  ) {
    fail("RUNNER_VERSION_MISMATCH");
  }
  const revision = spawnSync("git", ["rev-parse", "HEAD"], {
    cwd: WORKSPACE_ROOT,
    encoding: "utf8",
    env: {
      LANG: process.env.LANG ?? "C.UTF-8",
      PATH: process.env.PATH ?? "/usr/bin:/bin",
    },
    maxBuffer: 1024 * 1024,
  });
  const commit = revision.stdout?.trim();
  if (revision.status !== 0 || !/^[0-9a-f]{40}$/u.test(commit)) {
    fail("RUNNER_GIT_COMMIT_INVALID");
  }
  return { runnerSha256: sha256(runnerBytes), runnerGitCommit: commit };
}


function officialFrontendRoot(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    fail("OFFICIAL_FRONTEND_URL_INVALID");
  }
  if (
    url.toString() !== OFFICIAL_FRONTEND_ROOT ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    fail("OFFICIAL_FRONTEND_URL_INVALID");
  }
  return url;
}


async function fetchOfficialBytes(url, accept, errorCode) {
  let response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers: { Accept: accept },
      redirect: "error",
    });
  } catch {
    fail(errorCode);
  }
  if (!response.ok) {
    fail(errorCode);
  }
  const finalUrl = new URL(response.url);
  if (
    finalUrl.protocol !== "https:" ||
    finalUrl.hostname !== "lca.tiangong.earth" ||
    finalUrl.username ||
    finalUrl.password
  ) {
    fail("OFFICIAL_FRONTEND_URL_INVALID");
  }
  return Buffer.from(await response.arrayBuffer());
}


function uniqueMatch(text, pattern, code) {
  const matches = [...new Set(text.match(pattern) ?? [])];
  if (matches.length !== 1) {
    fail(code);
  }
  return matches[0];
}


function verifyFrontendSurfaceSemantics(mainText) {
  const required = [
    '.supabase.from(yt).select(`',
    'Je==="tg"?On=On.eq("state_code",100)',
    'Ba.t0==="lifecyclemodels"?59',
    'supabase.from("lifecyclemodels").select("json, json_tg,state_code,rule_verification,team_id")',
  ];
  if (required.some((marker) => !mainText.includes(marker))) {
    fail("FRONTEND_SURFACE_CONTRACT_MISSING");
  }
  return required;
}


async function fetchOfficialFrontend(frontendRootUrl) {
  const rootUrl = officialFrontendRoot(frontendRootUrl);
  const rootBytes = await fetchOfficialBytes(
    rootUrl,
    "text/html,application/xhtml+xml",
    "OFFICIAL_FRONTEND_ROOT_READ_FAILED",
  );
  const rootText = rootBytes.toString("utf8");
  const mainPath = uniqueMatch(
    rootText,
    /\/umi\.[0-9a-f]+\.js/gu,
    "FRONTEND_MAIN_BUNDLE_AMBIGUOUS",
  );
  const mainUrl = new URL(mainPath, rootUrl);
  const mainBytes = await fetchOfficialBytes(
    mainUrl,
    "application/javascript,text/javascript,*/*",
    "OFFICIAL_FRONTEND_BUNDLE_READ_FAILED",
  );
  const mainText = mainBytes.toString("utf8");
  const semanticMarkers = verifyFrontendSurfaceSemantics(mainText);
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
  return {
    rootUrl,
    rootBytes,
    mainUrl,
    mainBytes,
    semanticMarkers,
    publicBaseUrl,
    publicPublishableValue,
  };
}


async function createAuthenticatedReadClient(cliRoot) {
  process.env.TIANGONG_LCA_DISABLE_SESSION_CACHE = "true";
  if (!process.env.TIANGONG_LCA_API_KEY) {
    fail("AUTHENTICATED_RUNTIME_MISSING");
  }
  let clientModule;
  let sessionModule;
  try {
    [clientModule, sessionModule] = await Promise.all([
      import(
        pathToFileURL(path.join(cliRoot, "dist/src/lib/supabase-client.js")).href
      ),
      import(
        pathToFileURL(path.join(cliRoot, "dist/src/lib/supabase-session.js")).href
      ),
    ]);
  } catch {
    fail("PINNED_CLI_IMPORT_FAILED");
  }
  let authenticatedRuntime;
  try {
    const runtime = clientModule.requireSupabaseRestRuntime(process.env);
    authenticatedRuntime = sessionModule.createSupabaseDataRuntime({
      runtime,
      fetchImpl: fetch,
      timeoutMs: 30_000,
    });
    await authenticatedRuntime.getAccessToken();
    return clientModule.createSupabaseDataClient(
      authenticatedRuntime,
      fetch,
      30_000,
    ).client;
  } catch {
    fail("AUTHENTICATED_RUNTIME_INITIALIZATION_FAILED");
  }
}


function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}


function partialState(models, serverTotalCount, pagesRead, modelCap) {
  return { models, serverTotalCount, pagesRead, modelCap };
}


function normalizePublicModel(value) {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail("PUBLIC_MODEL_SHAPE_INVALID");
  }
  if (
    Object.keys(value).some((key) => !PUBLIC_MODEL_FIELDS.includes(key)) ||
    Object.keys(value).length !== PUBLIC_MODEL_FIELDS.length
  ) {
    fail("PUBLIC_MODEL_FIELD_INVALID");
  }
  if (
    typeof value.id !== "string" ||
    !value.id ||
    typeof value.version !== "string" ||
    !value.version ||
    value.state_code !== 100 ||
    value.json === null ||
    typeof value.json !== "object" ||
    typeof value.modified_at !== "string" ||
    !value.modified_at
  ) {
    fail("PUBLIC_MODEL_SHAPE_INVALID");
  }
  try {
    canonicalJson(value);
  } catch {
    fail("PUBLIC_MODEL_SHAPE_INVALID");
  }
  return Object.fromEntries(PUBLIC_MODEL_FIELDS.map((field) => [field, value[field]]));
}


function compareModelIdentity(left, right) {
  if (left.id !== right.id) {
    return left.id < right.id ? -1 : 1;
  }
  if (left.version === right.version) {
    return 0;
  }
  return left.version < right.version ? -1 : 1;
}


async function readPage(client, start, end, retryLimit) {
  for (let attempt = 0; attempt <= retryLimit; attempt += 1) {
    try {
      const response = await client
        .from("lifecyclemodels")
        .select(PUBLIC_MODEL_SELECT, { count: "exact" })
        .eq("state_code", 100)
        .order("id", { ascending: true })
        .order("version", { ascending: true })
        .range(start, end);
      if (!response?.error) {
        return response;
      }
    } catch {
      // Remote exception details are intentionally discarded.
    }
    if (attempt < retryLimit) {
      await delay(250 * 2 ** attempt);
    }
  }
  fail("SURFACE_PAGE_READ_FAILED");
}


export async function captureLifecycleModelPages(
  client,
  {
    pageSize = PAGE_SIZE,
    modelCap = MODEL_CAP,
    retryLimit = RETRY_LIMIT,
  } = {},
) {
  if (
    !client ||
    !Number.isInteger(pageSize) ||
    pageSize <= 0 ||
    !Number.isInteger(modelCap) ||
    modelCap <= 0 ||
    !Number.isInteger(retryLimit) ||
    retryLimit < 0
  ) {
    fail("CAPTURE_CONFIGURATION_INVALID");
  }
  const models = [];
  const identities = new Set();
  let serverTotalCount = null;
  let pagesRead = 0;
  while (serverTotalCount === null || models.length < serverTotalCount) {
    const start = models.length;
    let response;
    try {
      response = await readPage(client, start, start + pageSize - 1, retryLimit);
    } catch (error) {
      if (error instanceof Error && error.message === "SURFACE_PAGE_READ_FAILED") {
        error.partial = partialState(
          models,
          serverTotalCount,
          pagesRead,
          modelCap,
        );
      }
      throw error;
    }
    pagesRead += 1;
    if (
      !Array.isArray(response.data) ||
      !Number.isInteger(response.count) ||
      response.count < 0
    ) {
      fail(
        "SURFACE_PAGE_SHAPE_INVALID",
        partialState(models, serverTotalCount, pagesRead, modelCap),
      );
    }
    if (serverTotalCount === null) {
      serverTotalCount = response.count;
      if (serverTotalCount > modelCap) {
        fail(
          "DISCOVERY_MODEL_CAP_EXCEEDED",
          partialState(models, serverTotalCount, pagesRead, modelCap),
        );
      }
    } else if (response.count !== serverTotalCount) {
      fail(
        "SERVER_TOTAL_COUNT_CHANGED",
        partialState(models, serverTotalCount, pagesRead, modelCap),
      );
    }
    for (const rawModel of response.data) {
      let model;
      try {
        model = normalizePublicModel(rawModel);
      } catch (error) {
        if (error instanceof Error) {
          error.partial = partialState(
            models,
            serverTotalCount,
            pagesRead,
            modelCap,
          );
        }
        throw error;
      }
      const identity = `${model.id}@${model.version}`;
      if (identities.has(identity)) {
        fail(
          "DUPLICATE_MODEL_IDENTITY",
          partialState(models, serverTotalCount, pagesRead, modelCap),
        );
      }
      if (models.length > 0 && compareModelIdentity(models.at(-1), model) >= 0) {
        fail(
          "MODEL_ORDER_INVALID",
          partialState(models, serverTotalCount, pagesRead, modelCap),
        );
      }
      identities.add(identity);
      models.push(model);
    }
    if (models.length > serverTotalCount || models.length > modelCap) {
      fail(
        "SURFACE_COUNT_MISMATCH",
        partialState(models, serverTotalCount, pagesRead, modelCap),
      );
    }
    if (models.length < serverTotalCount && response.data.length < pageSize) {
      fail(
        "PAGINATION_INCOMPLETE",
        partialState(models, serverTotalCount, pagesRead, modelCap),
      );
    }
  }
  if (models.length !== serverTotalCount) {
    fail(
      "SURFACE_COUNT_MISMATCH",
      partialState(models, serverTotalCount, pagesRead, modelCap),
    );
  }
  return {
    models,
    serverTotalCount,
    pagesRead,
    paginationComplete: true,
  };
}


function artifactPath(absolute) {
  return path.relative(WORKSPACE_ROOT, absolute).split(path.sep).join("/");
}


function buildFrontendManifest(frontend, outputDir) {
  const rootPath = path.join(outputDir, "frontend", "index.html");
  const mainPath = path.join(
    outputDir,
    "frontend",
    path.basename(frontend.mainUrl.pathname),
  );
  const body = {
    contract: FRONTEND_CONTRACT,
    root_document: {
      url: frontend.rootUrl.toString(),
      sha256: sha256(frontend.rootBytes),
      bytes: frontend.rootBytes.length,
      artifact_path: artifactPath(rootPath),
    },
    main_bundle: {
      url: frontend.mainUrl.toString(),
      sha256: sha256(frontend.mainBytes),
      bytes: frontend.mainBytes.length,
      artifact_path: artifactPath(mainPath),
    },
    verified_surface: {
      data_source: "tg",
      table: "lifecyclemodels",
      state_code: 100,
      semantic_markers_sha256: sha256(
        Buffer.from(canonicalJson(frontend.semanticMarkers), "utf8"),
      ),
      public_runtime_recovered: true,
      public_runtime_values_persisted: false,
    },
  };
  return withContentIdentity(
    body,
    "frontend_content_sha256",
    "frontend_id",
  );
}


function buildCapture({
  observedAt,
  pageResult,
  runtime,
  frontendManifest,
  paginationComplete,
}) {
  const body = {
    contract: CAPTURE_CONTRACT,
    observed_at_utc: observedAt,
    surface: {
      data_source: "tg",
      table: "lifecyclemodels",
      state_code: 100,
      ordering: ["id:asc", "version:asc"],
      model_cap: MODEL_CAP,
      server_total_count: pageResult.serverTotalCount,
      captured_exact_version_count: pageResult.models.length,
      pagination_complete: paginationComplete,
    },
    runtime,
    frontend: {
      frontend_id: frontendManifest.frontend_id,
      frontend_content_sha256: frontendManifest.frontend_content_sha256,
      root_document_sha256: frontendManifest.root_document.sha256,
      main_bundle_sha256: frontendManifest.main_bundle.sha256,
      surface_semantics_verified: true,
      public_runtime_values_persisted: false,
    },
    models: pageResult.models,
    effects: { ...ZERO_EFFECTS },
  };
  return withContentIdentity(
    body,
    "surface_capture_content_sha256",
    "surface_capture_id",
  );
}


function writeFileNoClobber(target, bytes) {
  writeFileSync(target, bytes, { flag: "wx", mode: 0o600 });
  chmodSync(target, 0o600);
}


function writeCaptureDirectory({
  outputDir,
  frontend,
  frontendManifest,
  capture,
  receipt,
}) {
  const outputsRoot = path.resolve(WORKSPACE_ROOT, "outputs");
  validateOutputPath(outputDir, outputsRoot);
  mkdirSync(path.dirname(outputDir), { recursive: true, mode: 0o700 });
  validateOutputPath(outputDir, outputsRoot);
  try {
    mkdirSync(outputDir, { recursive: false, mode: 0o700 });
  } catch (error) {
    if (error?.code === "EEXIST") {
      fail("SURFACE_CAPTURE_ALREADY_EXISTS");
    }
    fail("SURFACE_CAPTURE_WRITE_FAILED");
  }
  chmodSync(outputDir, 0o700);
  const frontendDir = path.join(outputDir, "frontend");
  mkdirSync(frontendDir, { recursive: false, mode: 0o700 });
  chmodSync(frontendDir, 0o700);
  const jsonBytes = (value) =>
    Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
  try {
    writeFileNoClobber(path.join(frontendDir, "index.html"), frontend.rootBytes);
    writeFileNoClobber(
      path.join(frontendDir, path.basename(frontend.mainUrl.pathname)),
      frontend.mainBytes,
    );
    writeFileNoClobber(
      path.join(outputDir, "frontend-manifest.json"),
      jsonBytes(frontendManifest),
    );
    writeFileNoClobber(
      path.join(outputDir, "surface-capture.json"),
      jsonBytes(capture),
    );
    writeFileNoClobber(
      path.join(outputDir, "runtime-receipt.json"),
      jsonBytes(receipt),
    );
  } catch (error) {
    if (error instanceof Error && /^[A-Z0-9_]+$/u.test(error.message)) {
      throw error;
    }
    fail("SURFACE_CAPTURE_WRITE_FAILED");
  }
}


function fixedCode(error) {
  return error instanceof Error && /^[A-Z0-9_]{1,80}$/u.test(error.message)
    ? error.message
    : "LIFECYCLEMODEL_SURFACE_CAPTURE_FAILED";
}


function buildReceipt({ capture, pageResult, status, errorCode, runtime }) {
  const body = {
    contract: RECEIPT_CONTRACT,
    status,
    error_code: errorCode,
    surface_capture_id: capture.surface_capture_id,
    surface_capture_content_sha256: capture.surface_capture_content_sha256,
    server_total_count: capture.surface.server_total_count,
    captured_exact_version_count:
      capture.surface.captured_exact_version_count,
    pages: pageResult.pagesRead,
    pagination_complete: capture.surface.pagination_complete,
    model_cap: capture.surface.model_cap,
    runner_sha256: runtime.runner_sha256,
    runner_git_commit: runtime.runner_git_commit,
    effects: { ...ZERO_EFFECTS },
  };
  return withContentIdentity(body, "receipt_content_sha256", "receipt_id");
}


async function run() {
  const args = parseArgs(process.argv.slice(2));
  const outputDir = outputDirectory(args["--output-dir"]);
  const policy = validatePolicy(args["--policy"]);
  const adoption = validateAdoption(args["--adoption"]);
  if (policy.discovery_model_cap !== MODEL_CAP) {
    fail("POLICY_BINDING_INVALID");
  }
  const cliRuntime = verifyCliRuntime(args["--cli-root"], adoption);
  const runnerEvidence = assertVersionedRunnerBytes();
  const frontendRootUrl = officialFrontendRoot(args["--frontend-root"]).toString();
  const observedAt = new Date().toISOString();
  const frontend = await fetchOfficialFrontend(frontendRootUrl);
  const frontendManifest = buildFrontendManifest(frontend, outputDir);

  process.env.TIANGONG_LCA_API_BASE_URL = frontend.publicBaseUrl;
  process.env.TIANGONG_LCA_SUPABASE_PUBLISHABLE_KEY =
    frontend.publicPublishableValue;
  process.env.TIANGONG_LCA_DISABLE_SESSION_CACHE = "true";

  const runtime = {
    cli_package: CLI_PACKAGE,
    cli_version: CLI_VERSION,
    adoption_id: adoption.adoption_id,
    adoption_content_sha256: adoption.adoption_content_sha256,
    adoption_file_sha256: ADOPTION_FILE_SHA256,
    policy_file_sha256: POLICY_FILE_SHA256,
    npm_integrity: adoption.npm_integrity,
    package_lock_sha256: cliRuntime.packageLockSha256,
    node_modules_tree_sha256: cliRuntime.nodeModulesTreeSha256,
    runner_sha256: runnerEvidence.runnerSha256,
    runner_git_commit: runnerEvidence.runnerGitCommit,
    session_cache_disabled: true,
    authenticated_read_runtime_present: true,
    public_runtime_recovered: true,
    public_runtime_values_persisted: false,
  };
  const client = await createAuthenticatedReadClient(cliRuntime.cliRoot);
  let pageResult;
  let paginationComplete = true;
  let captureErrorCode = null;
  try {
    pageResult = await captureLifecycleModelPages(client, {
      pageSize: PAGE_SIZE,
      modelCap: MODEL_CAP,
      retryLimit: RETRY_LIMIT,
    });
  } catch (error) {
    captureErrorCode = fixedCode(error);
    paginationComplete = false;
    const partial = error?.partial;
    pageResult = {
      models: Array.isArray(partial?.models) ? partial.models : [],
      serverTotalCount:
        Number.isInteger(partial?.serverTotalCount)
          ? partial.serverTotalCount
          : null,
      pagesRead: Number.isInteger(partial?.pagesRead) ? partial.pagesRead : 0,
    };
  }
  const capture = buildCapture({
    observedAt,
    pageResult,
    runtime,
    frontendManifest,
    paginationComplete,
  });
  const status = paginationComplete ? "exhausted" : "partial";
  const receipt = buildReceipt({
    capture,
    pageResult,
    status,
    errorCode: captureErrorCode,
    runtime,
  });
  writeCaptureDirectory({
    outputDir,
    frontend,
    frontendManifest,
    capture,
    receipt,
  });
  process.stdout.write(
    `${JSON.stringify({
      status,
      surface_capture_id: capture.surface_capture_id,
      surface_capture_content_sha256: capture.surface_capture_content_sha256,
      server_total_count: capture.surface.server_total_count,
      captured_exact_version_count:
        capture.surface.captured_exact_version_count,
      pages: pageResult.pagesRead,
      pagination_complete: capture.surface.pagination_complete,
      output_path: artifactPath(outputDir),
    })}\n`,
  );
  if (!paginationComplete) {
    fail(captureErrorCode ?? "SURFACE_CAPTURE_PARTIAL");
  }
}


if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  run().catch((error) => {
    process.stderr.write(`${fixedCode(error)}\n`);
    process.exitCode = 1;
  });
}
