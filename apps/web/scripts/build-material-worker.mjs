import { build } from "esbuild";
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const output = resolve(root, "public", "material-hash.worker.js");

await mkdir(dirname(output), { recursive: true });
await build({
  entryPoints: [resolve(root, "src", "workers", "material-hash.worker.ts")],
  outfile: output,
  bundle: true,
  platform: "browser",
  format: "iife",
  target: ["es2022"],
  legalComments: "none",
  sourcemap: false,
  minify: true,
});
