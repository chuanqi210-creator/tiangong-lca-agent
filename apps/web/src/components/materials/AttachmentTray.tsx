"use client";

import { usePromptInputAttachments } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { copy } from "@/lib/copy";
import { RotateCcwIcon, SkipForwardIcon, UploadIcon, XIcon } from "lucide-react";

export function AttachmentTray({
  onRetry,
  onReselect,
}: {
  onRetry?: (id: string) => void | Promise<void>;
  onReselect?: (id: string) => void;
}) {
  const attachments = usePromptInputAttachments();
  if (attachments.files.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2 border-b border-border px-3 py-2" aria-label={copy.materials.tray}>
      {attachments.files.map((attachment) => (
        <div key={attachment.id} className="flex max-w-full items-center gap-2 rounded-md border border-border bg-muted px-2 py-1 text-xs text-foreground">
          <span className="truncate">{attachment.relativePath || attachment.filename}</span>
          <span className="shrink-0 text-muted-foreground">
            {attachment.omitted
              ? copy.materials.omitted
              : attachment.uploadStatus === "uploaded"
              ? copy.materials.uploaded
              : attachment.uploadStatus === "uploading" || attachment.uploadStatus === "hashing"
                ? copy.materials.uploading
                : attachment.uploadStatus === "failed"
                  ? copy.materials.failed
                  : copy.materials.selected}
          </span>
          {attachment.uploadError && !attachment.omitted && (
            <span role="alert" className="max-w-48 truncate text-destructive">
              {attachment.uploadError}
            </span>
          )}
          {attachment.uploadStatus === "failed" && !attachment.omitted && (
            <>
              <Button type="button" size="icon-xs" variant="ghost" onClick={() => void onRetry?.(attachment.id)} aria-label={copy.materials.retry}>
                <RotateCcwIcon className="size-3" />
              </Button>
              <Button type="button" size="icon-xs" variant="ghost" onClick={() => attachments.update(attachment.id, { omitted: true, uploadError: undefined })} aria-label={copy.materials.omit}>
                <SkipForwardIcon className="size-3" />
              </Button>
            </>
          )}
          {attachment.uploadStatus === "reselect_required" && (
            <>
              <Button type="button" size="icon-xs" variant="ghost" onClick={() => onReselect?.(attachment.id)} aria-label={copy.materials.reselect}>
                <UploadIcon className="size-3" />
              </Button>
              <Button type="button" size="icon-xs" variant="ghost" onClick={() => attachments.update(attachment.id, { omitted: true, uploadError: undefined })} aria-label={copy.materials.omit}>
                <SkipForwardIcon className="size-3" />
              </Button>
            </>
          )}
          <Button type="button" size="icon-xs" variant="ghost" onClick={() => attachments.remove(attachment.id)} aria-label={copy.materials.remove}>
            <XIcon className="size-3" />
          </Button>
        </div>
      ))}
    </div>
  );
}
