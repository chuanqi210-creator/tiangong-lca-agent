/** 用户源材料工具：内容保持用户输入身份，由后端冻结并内容寻址。 */

export interface StagedMaterial {
  name: string;
  text: string;
  mediaType?: string;
}

export interface SourceMaterialInput {
  name: string;
  media_type: string;
  sha256: string;
  authority: "user_supplied_project_input";
  content: { text: string };
}

const TEXT_EXTENSIONS = [".txt", ".md", ".csv", ".json"];

/** 无法提取文本的文件类型必须显式报错，不静默丢弃 */
export function isTextFile(file: File): boolean {
  const lower = file.name.toLowerCase();
  if (TEXT_EXTENSIONS.some((ext) => lower.endsWith(ext))) return true;
  return file.type.startsWith("text/") || file.type === "application/json";
}

export async function readMaterialFile(file: File): Promise<StagedMaterial> {
  if (!isTextFile(file)) {
    throw new Error(`「${file.name}」`);
  }
  const text = await file.text();
  if (!text.trim()) {
    throw new Error(`「${file.name}」（空文件）`);
  }
  return {
    name: file.name,
    // 空白只用于判断“是否为空”；哈希和后端冻结必须保留原始 UTF-8 文本。
    text,
    mediaType: file.type || undefined,
  };
}

async function sha256(text: string): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new Error("当前环境无法安全计算材料 SHA-256");
  }
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function materialMediaType(material: StagedMaterial): string {
  if (material.mediaType?.includes("/")) return material.mediaType;
  return material.name.toLowerCase().endsWith(".json")
    ? "application/json"
    : "text/plain";
}

/** Recreate the exact browser File boundary used by the resumable upload path. */
export function stagedMaterialsToFiles(materials: StagedMaterial[]): File[] {
  return materials.map(
    (material) =>
      new File([material.text], material.name, {
        type: materialMediaType(material),
      }),
  );
}

/** 编译严格的用户材料描述符；权威身份不可由调用方选择。 */
export async function buildSourceMaterials(
  materials: StagedMaterial[],
): Promise<SourceMaterialInput[]> {
  return Promise.all(
    materials.map(async (material) => ({
      name: material.name,
      media_type: materialMediaType(material),
      sha256: await sha256(material.text),
      authority: "user_supplied_project_input" as const,
      content: { text: material.text },
    })),
  );
}
