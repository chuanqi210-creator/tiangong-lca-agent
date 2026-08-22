"use client";

import { DownloadIcon, FileTextIcon } from "lucide-react";
import type { Attachment } from "@/components/chat/types";

/** 附件行：文件图标 + 文件名 + 弱化类型/大小 + 下载图标，hover 行底点亮 */
export function AttachmentCard({ items }: { items: Attachment[] }) {
  return (
    <div className="mt-2 space-y-0.5">
      {items.map((att) => (
        <a
          key={att.fileUrl}
          href={att.fileUrl}
          download
          className="group flex items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-muted/60"
        >
          <FileTextIcon
            aria-hidden="true"
            className="size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-chart-1"
          />
          <span className="min-w-0 flex-1 truncate text-sm text-foreground transition-colors group-hover:text-chart-1">
            {att.fileName}
          </span>
          <span className="shrink-0 font-mono text-[11px] text-muted-foreground/70 uppercase">
            {att.fileType}
          </span>
          <span className="shrink-0 font-mono text-[11px] text-muted-foreground/70 tabular-nums">
            {att.fileSize ? `${(att.fileSize / 1024).toFixed(1)} KB` : ""}
          </span>
          <DownloadIcon
            aria-hidden="true"
            className="size-3.5 shrink-0 text-muted-foreground transition-colors group-hover:text-chart-1"
          />
        </a>
      ))}
    </div>
  );
}
