import { createHash } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { env, execPath, stdout } from "node:process";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const MAX_PAGES = 50;
const TIMEOUT_MS = 15_000;
const SAFE_TOOL = "LCA_Calculation_Guidance_Tool";
const SECRET_SHAPE =
  /(authorization\s*:\s*bearer|api[_-]?key\s*=|(?:password|secret|token)\s*=)/i;

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

async function bounded(operation) {
  let timer;
  try {
    return await Promise.race([
      operation,
      new Promise((_, reject) => {
        timer = setTimeout(
          () => reject(new Error("bounded MCP timeout")),
          TIMEOUT_MS,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function listAll(label, pageCall, itemKey, identity) {
  const items = [];
  let cursor;
  for (let pageNumber = 0; pageNumber < MAX_PAGES; pageNumber += 1) {
    const page = await bounded(pageCall(cursor ? { cursor } : undefined));
    const pageItems = page[itemKey] ?? [];
    if (!Array.isArray(pageItems)) {
      throw new Error(`${label} page payload is invalid`);
    }
    items.push(...pageItems);
    cursor = page.nextCursor;
    if (!cursor) {
      const identities = items.map(identity);
      if (new Set(identities).size !== identities.length) {
        throw new Error(`duplicate MCP ${label} identity`);
      }
      return { items, pageCount: pageNumber + 1 };
    }
  }
  throw new Error(`${label} pagination exceeded ${MAX_PAGES} pages`);
}

async function optionalList(label, callback, itemKey, identity) {
  try {
    const listed = await listAll(label, callback, itemKey, identity);
    return {
      supported: true,
      count: listed.items.length,
      pageCount: listed.pageCount,
      identitySha256: sha256(
        listed.items.map(identity).sort().join("\n"),
      ),
    };
  } catch (error) {
    const message = String(error?.message ?? error);
    if (/method not found|not supported/i.test(message)) {
      return {
        supported: false,
        count: 0,
        pageCount: 0,
        identitySha256: sha256(""),
      };
    }
    throw error;
  }
}

async function main() {
  const runtimeRoot = dirname(fileURLToPath(import.meta.url));
  const serverBin = join(
    runtimeRoot,
    "node_modules",
    ".bin",
    "tiangong-lca-mcp-stdio",
  );
  const cliDir = join(
    runtimeRoot,
    "node_modules",
    "@tiangong-lca",
    "cli",
  );
  const childEnvironment = {
    LC_ALL: "C",
    NODE_ENV: "production",
    PATH: `${join(runtimeRoot, "node_modules", ".bin")}:${env.PATH ?? ""}`,
    TIANGONG_LCA_CLI_DIR: cliDir,
  };
  const transport = new StdioClientTransport({
    command: serverBin,
    args: [],
    cwd: runtimeRoot,
    env: childEnvironment,
    stderr: "pipe",
  });
  let stderrText = "";
  transport.stderr?.on("data", (chunk) => {
    stderrText = `${stderrText}${chunk.toString()}`.slice(-4096);
  });
  const client = new Client(
    { name: "d18-runtime-health", version: "1.0.0" },
    { capabilities: {} },
  );
  try {
    await bounded(client.connect(transport));
    const tools = await listAll(
      "tool",
      (params) => client.listTools(params),
      "tools",
      (item) => item.name,
    );
    const toolNames = tools.items.map((item) => item.name).sort();
    if (!toolNames.includes(SAFE_TOOL)) {
      throw new Error("safe MCP guidance tool is absent");
    }
    const schemaHashes = Object.fromEntries(
      tools.items
        .map((item) => [
          item.name,
          sha256(JSON.stringify(item.inputSchema ?? {})),
        ])
        .sort(([left], [right]) => left.localeCompare(right)),
    );
    const prompts = await optionalList(
      "prompt",
      (params) => client.listPrompts(params),
      "prompts",
      (item) => item.name,
    );
    const resources = await optionalList(
      "resource",
      (params) => client.listResources(params),
      "resources",
      (item) => item.uri,
    );
    const firstPrompt = prompts.supported
      ? await bounded(client.listPrompts())
      : { prompts: [] };
    const promptName = firstPrompt.prompts?.[0]?.name;
    const promptReadResult = promptName
      ? await bounded(client.getPrompt({ name: promptName, arguments: {} }))
      : null;
    const firstResource = resources.supported
      ? await bounded(client.listResources())
      : { resources: [] };
    const resourceUri = firstResource.resources?.[0]?.uri;
    const resourceReadResult = resourceUri
      ? await bounded(client.readResource({ uri: resourceUri }))
      : null;
    const safeCallResult = await bounded(
      client.callTool({ name: SAFE_TOOL, arguments: {} }),
    );
    const serializedCall = JSON.stringify(safeCallResult);
    const serializedPrompt = JSON.stringify(promptReadResult);
    const serializedResource = JSON.stringify(resourceReadResult);
    if (
      SECRET_SHAPE.test(serializedCall) ||
      SECRET_SHAPE.test(serializedPrompt) ||
      SECRET_SHAPE.test(serializedResource) ||
      SECRET_SHAPE.test(stderrText)
    ) {
      throw new Error("MCP probe produced sensitive output");
    }
    const report = {
      contract: "lca_research_kernel.mcp_runtime_probe/v1",
      client: {
        name: "d18-runtime-health",
        sdkVersion: "1.29.0",
        nodeVersion: env.NODE_VERSION ?? null,
        executableSha256: sha256(execPath),
      },
      server: {
        package: "@tiangong-lca/mcp-server",
        version: "0.0.31",
        transport: "stdio",
      },
      tools: {
        count: tools.items.length,
        pageCount: tools.pageCount,
        identitySha256: sha256(toolNames.join("\n")),
        schemaHashes,
      },
      prompts,
      resources,
      promptRead: {
        name: promptName ?? null,
        resultSha256: sha256(serializedPrompt),
        performed: promptReadResult !== null,
      },
      resourceRead: {
        uri: resourceUri ?? null,
        resultSha256: sha256(serializedResource),
        performed: resourceReadResult !== null,
      },
      safeCall: {
        tool: SAFE_TOOL,
        resultSha256: sha256(serializedCall),
        contentBlocks: Array.isArray(safeCallResult.content)
          ? safeCallResult.content.length
          : 0,
        isError: safeCallResult.isError === true,
      },
      stderrSha256: sha256(stderrText),
      forbiddenToolCalls: 0,
      externalWrites: 0,
      scientificResultsCreated: 0,
    };
    stdout.write(`${JSON.stringify(report)}\n`);
  } finally {
    await transport.close();
  }
}

main().catch((error) => {
  const message = String(error?.message ?? error);
  if (SECRET_SHAPE.test(message)) {
    throw new Error("MCP probe failed with redacted sensitive output");
  }
  throw error;
});
