import { createHash } from "node:crypto";
import { stdout } from "node:process";
import * as sdk from "@tiangong-lca/tidas-sdk";
import * as schemas from "@tiangong-lca/tidas-sdk/schemas";
import * as tools from "@tiangong-lca/tidas-sdk/tools";

function identity(module) {
  const exports = Object.keys(module).sort();
  return {
    exportCount: exports.length,
    exportIdentitySha256: createHash("sha256")
      .update(exports.join("\n"))
      .digest("hex"),
  };
}

stdout.write(
  `${JSON.stringify({
    contract: "lca_research_kernel.tidas_sdk_runtime_probe/v1",
    package: "@tiangong-lca/tidas-sdk",
    version: "0.1.45",
    surfaces: {
      root: identity(sdk),
      schemas: identity(schemas),
      tools: identity(tools),
    },
    importsPerformed: 3,
    externalWrites: 0,
    scientificResultsCreated: 0,
  })}\n`,
);
