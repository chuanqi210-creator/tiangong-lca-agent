import type { EvidenceCitation } from "./types";

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function integer(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

export function citationLocatorLabel(citation: EvidenceCitation): string {
  const locator = citation.locator;
  let location = "精确位置";
  switch (locator.kind) {
    case "pdf_region": {
      const page = integer(locator.page);
      location = page === null ? "PDF 区域" : `第 ${page} 页`;
      break;
    }
    case "document_section": {
      const headings = Array.isArray(locator.heading_path)
        ? locator.heading_path.filter((item): item is string => typeof item === "string")
        : [];
      const start = integer(locator.paragraph_start);
      const end = integer(locator.paragraph_end);
      const paragraph = start === null
        ? null
        : start === end || end === null
          ? `段落 ${start}`
          : `段落 ${start}–${end}`;
      location = [...headings, paragraph].filter(Boolean).join(" · ") || "文档章节";
      break;
    }
    case "spreadsheet_range": {
      location = [text(locator.sheet_name), text(locator.cell_range)]
        .filter(Boolean)
        .join(" · ") || "表格区域";
      break;
    }
    case "text_span": {
      const start = integer(locator.start_line);
      const end = integer(locator.end_line);
      location = start === null
        ? "文本片段"
        : start === end || end === null
          ? `第 ${start} 行`
          : `第 ${start}–${end} 行`;
      break;
    }
    case "json_pointer":
      location = text(locator.pointer) ?? "JSON 路径";
      break;
    case "xml_xpath":
      location = text(locator.xpath) ?? "XML 路径";
      break;
    case "archive_member":
      location = text(locator.member_path) ?? "压缩包成员";
      break;
    case "lca_object":
      location = [
        text(locator.dataset_uuid),
        text(locator.version),
        text(locator.process_id),
        text(locator.flow_id),
        text(locator.unit),
        text(locator.provider),
      ].filter(Boolean).join(" · ") || "LCA 对象";
      break;
    case "audio_segment": {
      const start = integer(locator.start_ms);
      const end = integer(locator.end_ms);
      location = start === null ? "音频片段" : `${start}–${end ?? start} ms`;
      break;
    }
    case "image_region":
      location = "图像区域";
      break;
  }
  return `${citation.displayName} · ${location}`;
}
