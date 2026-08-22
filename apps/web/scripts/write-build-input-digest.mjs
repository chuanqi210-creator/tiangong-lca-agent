import { createHash } from "node:crypto";
import { readdir, readFile, writeFile } from "node:fs/promises";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const inputs = [
  "src",
  "public",
  "scripts",
  "package.json",
  "pnpm-lock.yaml",
  "pnpm-workspace.yaml",
  "next.config.ts",
  "postcss.config.mjs",
  "tsconfig.json",
];

async function files(path) {
  const entries = await readdir(path, { withFileTypes: true });
  const nested = await Promise.all(entries.map((entry) => {
    const target = join(path, entry.name);
    return entry.isDirectory() ? files(target) : [target];
  }));
  return nested.flat();
}

const selected = [];
for (const input of inputs) {
  const target = join(root, input);
  try {
    selected.push(...(await files(target)));
  } catch (error) {
    if (error?.code === "ENOTDIR") selected.push(target);
    else throw error;
  }
}
selected.sort((left, right) => relative(root, left).localeCompare(relative(root, right)));
const hash = createHash("sha256");
for (const path of selected) {
  hash.update(relative(root, path));
  hash.update("\0");
  hash.update(await readFile(path));
  hash.update("\0");
}
await writeFile(join(root, ".next/d18-input-digest"), `${hash.digest("hex")}\n`);
