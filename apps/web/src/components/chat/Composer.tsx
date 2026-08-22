"use client";

import { useEffect, useRef, useState } from "react";
import {
  PromptInput,
  PromptInputFooter,
  PromptInputProvider,
  PromptInputSubmit,
  PromptInputTextarea,
  usePromptInputController,
} from "@/components/ai-elements/prompt-input";
import { AttachmentTray } from "@/components/materials/AttachmentTray";
import { MaterialAddMenu } from "@/components/materials/MaterialAddMenu";
import { copy } from "@/lib/copy";
import { attachmentSubmissionDecision, type ComposerSubmission } from "@/lib/d18/material-types";
import { compileComposerSubmission } from "@/lib/d18/upload-manager";
import { resumeMaterialUpload, uploadMaterialFiles } from "@/lib/d18/material-client";
import { loadUploadSessions } from "@/lib/d18/upload-session-store";
import type { PromptInputAttachment } from "@/components/ai-elements/prompt-input";
import { readDraft, useDraftActions } from "@/lib/use-draft";

/** 把 Provider 内的文本变化同步到草稿存储（跳过挂载时的恢复值） */
function DraftSync({ setDraft }: { setDraft: (text: string) => void }) {
  const controller = usePromptInputController();
  const value = controller.textInput.value;
  const isFirstRender = useRef(true);

  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    setDraft(value);
  }, [value, setDraft]);
  return null;
}

/** 提交/停止按钮：读 Provider 文本判空；运行中变停止键走 onStop */
function SubmitButton({
  busy,
  running,
  onStop,
}: {
  busy: boolean;
  running: boolean;
  onStop?: () => void;
}) {
  const controller = usePromptInputController();
  const empty = controller.textInput.value.trim().length === 0;
  const attachmentsEmpty = controller.attachments.files.length === 0;
  return (
    <PromptInputSubmit
      className="rounded-full"
      status={running ? "streaming" : undefined}
      onStop={onStop}
      disabled={running ? busy : busy || (empty && attachmentsEmpty)}
      title={running ? copy.research.composerStopLabel : undefined}
    />
  );
}

function ComposerInner({
  draftId,
  disabled,
  inputDisabled,
  placeholder,
  running,
  onSend,
  onStop,
  researchId,
}: {
  draftId: string;
  disabled: boolean;
  inputDisabled: boolean;
  placeholder: string;
  running: boolean;
  onSend: (submission: ComposerSubmission) => void;
  onStop?: () => void;
  researchId: string;
}) {
  const { setDraft, clearDraft } = useDraftActions(draftId);
  const [scope, setScope] = useState<"turn" | "project">("project");
  const [selectionError, setSelectionError] = useState("");
  const controller = usePromptInputController();
  const reselectInputRef = useRef<HTMLInputElement | null>(null);
  const reselectAttachmentRef = useRef<string | null>(null);

  const updateProgress = (attachmentId: string, progress: { status: "hashing" | "uploading" | "uploaded" | "failed"; materialId?: string; error?: string }) => {
    controller.attachments.update(attachmentId, {
      uploadStatus: progress.status,
      materialId: progress.materialId,
      uploadError: progress.error,
    });
  };

  const resumeReselected = async (attachmentId: string, file: File) => {
    const attachment = controller.attachments.files.find((item) => item.id === attachmentId);
    if (!attachment?.uploadId || !attachment.fingerprint || !attachment.expectedSha256 || attachment.size === undefined || attachment.lastModified === undefined) {
      throw new Error("upload session metadata is unavailable");
    }
    try {
      const materialId = await resumeMaterialUpload(
        {
          projectId: researchId,
          uploadId: attachment.uploadId,
          fingerprint: attachment.fingerprint,
          filename: attachment.filename ?? file.name,
          size: attachment.size,
          lastModified: attachment.lastModified,
          expectedSha256: attachment.expectedSha256,
          status: "reselect_required",
          partSize: attachment.partSize,
        },
        file,
        scope,
        (progress) => updateProgress(attachmentId, progress),
      );
      controller.attachments.update(attachmentId, { file, materialId, omitted: false, uploadStatus: "uploaded", uploadError: undefined });
    } catch (error) {
      controller.attachments.update(attachmentId, {
        file: undefined,
        uploadStatus: "reselect_required",
        uploadError: error instanceof Error ? error.message : copy.materials.reselect,
      });
    }
  };

  return (
    <>
      <DraftSync setDraft={setDraft} />
      <PromptInput
        accept="text/*,application/pdf,application/json,application/xml,application/zip,application/vnd.*,application/octet-stream,image/*,audio/*"
        batchMaxBytes={500 * 1024 * 1024}
        maxFiles={10}
        maxFileSize={100 * 1024 * 1024}
        multiple
        className="[&>[data-slot=input-group]]:rounded-2xl [&>[data-slot=input-group]]:border-border [&>[data-slot=input-group]]:bg-card [&>[data-slot=input-group]]:shadow-lg [&>[data-slot=input-group]]:shadow-black/20 dark:[&>[data-slot=input-group]]:bg-card"
        onError={(error) => {
          const message = {
            accept: copy.materials.policyType,
            max_file_size: copy.materials.policyFileSize,
            max_files: copy.materials.policyBatchCount,
            batch_size: copy.materials.policyBatchSize,
          }[error.code];
          setSelectionError(message);
        }}
        onSubmit={async (message) => {
          const text = message.text.trim();
          const decision = attachmentSubmissionDecision(
            message.files.map((file) => ({
              status: file.uploadStatus ?? "selected",
              materialId: file.materialId,
              omitted: file.omitted ?? false,
            })),
          );
          if (!decision.ready) throw new Error("failed attachments require retry, remove, or omit");
          let materialIds = decision.materialIds;
          if ((!text && message.files.length === 0) || disabled || inputDisabled) {
            throw new Error("composer submission is not ready");
          }
          const pending = message.files.filter((file) => !file.materialId && !file.omitted);
          if (pending.length > 0) {
            if (pending.some((attachment) => !attachment.file)) {
              throw new Error("chosen attachment bytes are unavailable");
            }
            materialIds = [
              ...materialIds,
              ...await uploadMaterialFiles(
                researchId,
                pending.map((attachment) => attachment.file!),
                scope,
                (progress) => {
                  const attachment = pending.find((item) => item.file === progress.file);
                  if (attachment) {
                    controller.attachments.update(attachment.id, {
                      uploadStatus: progress.status,
                      materialId: progress.materialId,
                      uploadError: progress.error,
                    });
                  }
                },
              ),
            ];
          }
          const submission = compileComposerSubmission(text, materialIds);
          onSend({ text: submission.text, materialIds: submission.materialIds });
          clearDraft();
        }}
      >
        <input
          ref={reselectInputRef}
          type="file"
          className="hidden"
          aria-label={copy.materials.reselect}
          onChange={(event) => {
            const attachmentId = reselectAttachmentRef.current;
            const file = event.currentTarget.files?.[0];
            event.currentTarget.value = "";
            if (attachmentId && file) void resumeReselected(attachmentId, file);
          }}
        />
        {selectionError && <p role="alert" className="px-3 pt-2 text-xs text-destructive">{selectionError}</p>}
        <AttachmentTray
          onRetry={(attachmentId) => {
            const attachment = controller.attachments.files.find((item) => item.id === attachmentId);
            if (attachment?.uploadId) {
              reselectAttachmentRef.current = attachmentId;
              reselectInputRef.current?.click();
            } else if (attachment?.file) {
              controller.attachments.update(attachmentId, { uploadStatus: "selected", uploadError: undefined, omitted: false });
            } else {
              reselectAttachmentRef.current = attachmentId;
              reselectInputRef.current?.click();
            }
          }}
          onReselect={(attachmentId) => {
            reselectAttachmentRef.current = attachmentId;
            reselectInputRef.current?.click();
          }}
        />
        <PromptInputTextarea
          placeholder={placeholder}
          disabled={disabled || inputDisabled}
        />
        <PromptInputFooter>
          <MaterialAddMenu scope={scope} onScopeChange={setScope} />
          <span className="hidden px-1 text-[11px] text-muted-foreground/70 sm:inline">
            {copy.research.composerHint}
          </span>
          <SubmitButton busy={disabled} running={running} onStop={onStop} />
        </PromptInputFooter>
      </PromptInput>
    </>
  );
}

/**
 * 输入区：ai-elements PromptInput 家族——自动增高（上限约 200px 后内部滚动）、
 * Enter 发送 / Shift+Enter 换行 / IME 合成中不发送；任务运行中提交键变为停止键。
 * 草稿按 draftId（researchId）持久化：挂载恢复、防抖写入、发送清空。
 * localStorage 仅客户端可读，挂载前先渲染同尺寸占位，避免水合不一致。
 */
export function Composer({
  disabled,
  inputDisabled = false,
  placeholder,
  running = false,
  draftId,
  onSend,
  onStop,
}: {
  disabled: boolean;
  /** 后端当前不接收普通文本；不清空已保存草稿，也不影响停止按钮 */
  inputDisabled?: boolean;
  placeholder: string;
  /** 任务进行中：提交键变为停止键 */
  running?: boolean;
  /** 草稿命名空间（researchId） */
  draftId: string;
  onSend: (submission: ComposerSubmission) => void;
  onStop?: () => void;
}) {
  const [mounted, setMounted] = useState(false);
  const [restoredAttachments, setRestoredAttachments] = useState<PromptInputAttachment[]>([]);
  useEffect(() => {
    let current = true;
    void loadUploadSessions()
      .then((sessions) => {
        if (!current) return;
        setRestoredAttachments(
          sessions
            .filter((session) => session.projectId === draftId)
            .map((session) => ({
              id: session.uploadId,
              type: "file" as const,
              url: "",
              filename: session.filename,
              mediaType: "application/octet-stream",
              uploadStatus: "reselect_required" as const,
              uploadError: copy.materials.reselect,
              uploadId: session.uploadId,
              fingerprint: session.fingerprint,
              expectedSha256: session.expectedSha256,
              size: session.size,
              lastModified: session.lastModified,
              partSize: session.partSize,
            })),
        );
      })
      .finally(() => {
        if (current) setMounted(true);
      });
    return () => { current = false; };
  }, [draftId]);

  return (
    <div className="px-4 pb-5 pt-1 sm:px-6">
      <div className="mx-auto w-full max-w-3xl">
        {mounted ? (
          <PromptInputProvider key={draftId} initialInput={readDraft(draftId)} initialAttachments={restoredAttachments}>
            <ComposerInner
              draftId={draftId}
              disabled={disabled}
              inputDisabled={inputDisabled}
              placeholder={placeholder}
              running={running}
              onSend={onSend}
              onStop={onStop}
              researchId={draftId}
            />
          </PromptInputProvider>
        ) : (
          <div
            aria-hidden
            className="min-h-[118px] rounded-2xl border border-border bg-card shadow-lg shadow-black/20"
          />
        )}
      </div>
    </div>
  );
}
