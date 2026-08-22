import type { SourceUse } from "./types";

export interface GroupedSourceUse {
  used: SourceUse[];
  considered: SourceUse[];
  partial: SourceUse[];
  conflicts: SourceUse[];
}

export function sourceAuthorityLabel(authority: string): string {
  if (authority.startsWith("tiangong")) return "TianGong 官方";
  if (authority === "user_supplied_project_input") return "用户项目材料";
  return "外部候选";
}

export function groupSourceUse(rows: SourceUse[]): GroupedSourceUse {
  const grouped: GroupedSourceUse = {
    used: [],
    considered: [],
    partial: [],
    conflicts: [],
  };
  for (const row of rows) {
    switch (row.status) {
      case "used":
        grouped.used.push(row);
        break;
      case "available_not_used":
        grouped.considered.push(row);
        break;
      case "unavailable_partial":
        grouped.partial.push(row);
        break;
      case "conflict":
        grouped.conflicts.push(row);
        break;
    }
  }
  const groups: SourceUse[][] = [
    grouped.used,
    grouped.considered,
    grouped.partial,
    grouped.conflicts,
  ];
  for (const values of groups) {
    values.sort((left, right) => left.displayName.localeCompare(right.displayName));
  }
  return grouped;
}
