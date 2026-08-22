/**
 * 成果文件生成：手写一个最小合法单页 PDF（约 1-2KB）与一个 CSV 清单。
 * 内容全部为演示数据。PDF 使用内置 Helvetica，仅含 ASCII 文本。
 */

function escapePdfText(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
}

const REPORT_LINES = [
  "LCA Research Report (Demo)",
  "",
  "Study: Carbon footprint of a LiFePO4 cathode material",
  "Functional unit: 1 kg LFP cathode material",
  "System boundary: cradle-to-gate",
  "",
  "Result (demo data): 8.6 kg CO2e per kg (path A, with user-supplied",
  "coating energy data). Limited result range (path B): 8.2 - 9.7 kg CO2e.",
  "",
  "Basis:",
  "- Background data: TianGong Database v1.2 (formal data)",
  "- Coating process energy: user-supplied record (formal data)",
  "  or literature candidate 0.42 kWh/kg (candidate, not endorsed",
  "  by TianGong)",
  "- Aggregation: calculation tool, units checked",
  "",
  "Limits:",
  "- Cradle-to-gate only; use and end-of-life excluded",
  "- Missing measured coating energy limits conclusion strength",
  "- All figures are demo data, not a real product declaration",
];

/** 手写最小合法 PDF：catalog / pages / page / contents / font，含 xref 表 */
export function buildReportPdf(): Uint8Array {
  const enc = new TextEncoder();

  const ops: string[] = ["BT", "/F1 13 Tf", "50 790 Td", "16 TL"];
  REPORT_LINES.forEach((line, i) => {
    if (i > 0) ops.push("T*");
    if (line) ops.push(`(${escapePdfText(line)}) Tj`);
  });
  ops.push("ET");
  const content = ops.join("\n");

  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] " +
      "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];

  let pdf = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((body, i) => {
    offsets.push(enc.encode(pdf).length);
    pdf += `${i + 1} 0 obj\n${body}\nendobj\n`;
  });

  const xrefStart = enc.encode(pdf).length;
  let xref = `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const off of offsets) {
    xref += `${String(off).padStart(10, "0")} 00000 n \n`;
  }
  pdf += `${xref}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefStart}\n%%EOF\n`;

  return enc.encode(pdf);
}

const CSV_ROWS = [
  ["阶段", "数据项", "数值", "单位", "数据类别", "来源"],
  ["原材料", "磷酸铁锂前驱体生产", "6.2", "kg CO2e/kg", "正式数据", "TianGong 数据库 v1.2"],
  ["能源", "电力消耗（华中电网）", "1.4", "kg CO2e/kg", "正式数据", "TianGong 数据库 v1.2"],
  ["生产", "涂布工序能耗", "0.42", "kWh/kg", "候选", "公开文献（演示）· 未获 TianGong 背书"],
  ["生产", "涂布工序能耗（用户补充）", "0.38", "kWh/kg", "正式数据", "用户补充材料"],
  ["生产", "涂布工序良率", "97", "%", "假设", "Agent 假设 · 报告中已标注"],
  ["汇总", "碳足迹合计（路径 A）", "8.6", "kg CO2e/kg", "计算", "计算工具 · 单位已核对"],
  ["汇总", "碳足迹区间（路径 B）", "8.2-9.7", "kg CO2e/kg", "计算", "计算工具 · 受限结论"],
];

export function buildInventoryCsv(): Uint8Array {
  const body = CSV_ROWS.map((row) =>
    row.map((cell) => `"${cell.replace(/"/g, '""')}"`).join(","),
  ).join("\r\n");
  // 加 BOM，保证 Excel 正确识别 UTF-8 中文
  return new TextEncoder().encode("﻿" + body + "\r\n");
}

export interface DeliverableFile {
  routeName: string;
  fileName: string;
  fileType: string;
  mimeType: string;
  build: () => Uint8Array;
}

export const deliverables: DeliverableFile[] = [
  {
    routeName: "lfp-carbon-report.pdf",
    fileName: "LFP正极材料碳足迹核算报告（演示）.pdf",
    fileType: "pdf",
    mimeType: "application/pdf",
    build: buildReportPdf,
  },
  {
    routeName: "lfp-carbon-inventory.csv",
    fileName: "碳足迹清单数据（演示）.csv",
    fileType: "excel",
    mimeType: "text/csv",
    build: buildInventoryCsv,
  },
];

export function findDeliverable(name: string): DeliverableFile | undefined {
  return deliverables.find((d) => d.routeName === name);
}
