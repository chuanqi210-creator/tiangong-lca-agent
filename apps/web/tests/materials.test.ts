import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSourceMaterials,
  readMaterialFile,
  stagedMaterialsToFiles,
  type StagedMaterial,
} from "../src/lib/d18/materials.ts";


test("user material stays user supplied and never acquires official identity", async () => {
  const materials: StagedMaterial[] = [
    { name: "input.txt", text: "用户提供的镀锌板质量数据。" },
  ];
  const result = await buildSourceMaterials(materials);

  assert.equal(result.length, 1);
  assert.deepEqual(Object.keys(result[0]).sort(), [
    "authority",
    "content",
    "media_type",
    "name",
    "sha256",
  ]);
  assert.equal(result[0].authority, "user_supplied_project_input");
  assert.equal(result[0].name, "input.txt");
  assert.equal(result[0].media_type, "text/plain");
  assert.equal(result[0].content.text, materials[0].text);
  assert.match(result[0].sha256, /^[0-9a-f]{64}$/);

  const serialized = JSON.stringify({
    objective: "分析用户材料。",
    source_materials: result,
  });
  assert.doesNotMatch(serialized, /D18-SOURCE-MATERIAL/);
  assert.doesNotMatch(serialized, /official_object_ids/);
  assert.doesNotMatch(serialized, /"authority":"TianGong"/);
});

test("file intake preserves leading and trailing bytes while rejecting whitespace-only files", async () => {
  const text = "\n  exact material  \n";
  const material = await readMaterialFile(
    new File([text], "exact.txt", { type: "text/plain" }),
  );
  assert.equal(material.text, text);
  await assert.rejects(
    readMaterialFile(new File([" \n\t"], "empty.txt", { type: "text/plain" })),
    /空文件/,
  );
});

test("json-looking material is preserved byte for byte as user text", async () => {
  const text = '{"declared_values":[{"identity":"mass","value":1,"unit":"kg"}]}';
  const [result] = await buildSourceMaterials([
    { name: "input.json", text },
  ]);
  assert.equal(result.media_type, "application/json");
  assert.deepEqual(result.content, { text });
});

test("project intake converts staged text to uploadable files without changing bytes", async () => {
  const text = "\n{\"rows\":[1,2,3]}\n";
  const [file] = stagedMaterialsToFiles([
    { name: "exact-process-rows.json", text, mediaType: "application/json" },
  ]);

  assert.equal(file.name, "exact-process-rows.json");
  assert.equal(file.type, "application/json");
  assert.equal(await file.text(), text);
});
