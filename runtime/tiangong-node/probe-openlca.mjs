import { createHash } from "node:crypto";
import { stdout } from "node:process";
import { IpcClient, RefType } from "olca-ipc";

const ENDPOINT = "http://127.0.0.1:18080";
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
          () => reject(new Error("bounded openLCA timeout")),
          TIMEOUT_MS,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function summarize(rows) {
  const identities = rows
    .map((row) => `${row.id ?? ""}\t${row.name ?? ""}`)
    .sort();
  return {
    count: rows.length,
    identitySha256: sha256(identities.join("\n")),
  };
}

async function main() {
  const client = IpcClient.on(ENDPOINT);
  const impactMethods = await bounded(
    client.getDescriptors(RefType.ImpactMethod),
  );
  const processes = await bounded(client.getDescriptors(RefType.Process));
  const report = {
    contract: "lca_research_kernel.openlca_ipc_probe/v1",
    endpoint: ENDPOINT,
    client: { package: "olca-ipc", version: "2.6.2" },
    impactMethods: summarize(impactMethods),
    processes: summarize(processes),
    readOnlyCalls: 2,
    externalWrites: 0,
    calculations: 0,
    scientificResultsCreated: 0,
  };
  stdout.write(`${JSON.stringify(report)}\n`);
}

await main();
