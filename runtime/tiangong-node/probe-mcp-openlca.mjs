import { createHash } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { env, stdout } from "node:process";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const ENDPOINT = "http://127.0.0.1:18080";
const SAFE_TOOLS = [
  "OpenLCA_List_LCIA_Methods_Tool",
  "OpenLCA_List_System_Processes_Tool",
];
const TIMEOUT_MS = 15_000;

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
          () => reject(new Error("bounded MCP/openLCA timeout")),
          TIMEOUT_MS,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function main() {
  const runtimeRoot = dirname(fileURLToPath(import.meta.url));
  const transport = new StdioClientTransport({
    command: join(
      runtimeRoot,
      "node_modules",
      ".bin",
      "tiangong-lca-mcp-stdio",
    ),
    args: [],
    cwd: runtimeRoot,
    env: {
      LC_ALL: "C",
      NODE_ENV: "production",
      PATH: `${join(runtimeRoot, "node_modules", ".bin")}:${env.PATH ?? ""}`,
      TIANGONG_LCA_CLI_DIR: join(
        runtimeRoot,
        "node_modules",
        "@tiangong-lca",
        "cli",
      ),
    },
    stderr: "pipe",
  });
  const client = new Client(
    { name: "d18-openlca-read-health", version: "1.0.0" },
    { capabilities: {} },
  );
  try {
    await bounded(client.connect(transport));
    const summaries = [];
    for (const name of SAFE_TOOLS) {
      const result = await bounded(
        client.callTool({ name, arguments: { serverUrl: ENDPOINT } }),
      );
      const serialized = JSON.stringify(result);
      summaries.push({
        name,
        resultSha256: sha256(serialized),
        contentBlocks: Array.isArray(result.content) ? result.content.length : 0,
        isError: result.isError === true,
      });
    }
    stdout.write(
      `${JSON.stringify({
        contract: "lca_research_kernel.mcp_openlca_read_probe/v1",
        endpoint: ENDPOINT,
        server: {
          package: "@tiangong-lca/mcp-server",
          version: "0.0.31",
          transport: "stdio",
        },
        calls: summaries,
        externalWrites: 0,
        calculations: 0,
        scientificResultsCreated: 0,
      })}\n`,
    );
  } finally {
    await transport.close();
  }
}

await main();
