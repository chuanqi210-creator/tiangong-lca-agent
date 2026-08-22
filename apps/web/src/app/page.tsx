"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
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
import { CommissionComposer } from "@/components/research/CommissionComposer";
import { ResearchTaskLibrary } from "@/components/research/ResearchTaskLibrary";
import { UsageGuide } from "@/components/research/UsageGuide";
import {
  createCommissionDraft,
  type CommissionDraft,
} from "@/lib/commission-draft";
import { copy } from "@/lib/copy";
import { createResearchForNamespace } from "@/lib/registry";
import { updateResearch } from "@/lib/registry";
import type { ResearchTaskId } from "@/lib/research-task-library";
import { compileComposerSubmission } from "@/lib/d18/upload-manager";
import { uploadMaterialFiles, type MaterialUploadProgress } from "@/lib/d18/material-client";
import { attachmentSubmissionDecision, type MaterialScope } from "@/lib/d18/material-types";
import {
  connectD18Session,
  ensureD18Session,
  type D18Connection,
} from "@/lib/session-client";

const reveal = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
};

const microLabel = "font-mono text-[11px] tracking-widest uppercase";

/** 首屏大输入框：Composer 同款悬浮容器，提交即创建研究并进入研究页 */
export function HeroInput({
  creating,
  onSubmit,
}: {
  creating: boolean;
  onSubmit: (
    text: string,
    files: File[],
    scope: MaterialScope,
    completedMaterialIds: string[],
    onProgress: (progress: MaterialUploadProgress) => void,
  ) => Promise<void>;
}) {
  return (
    <PromptInputProvider initialInput="">
      <HeroInputInner creating={creating} onSubmit={onSubmit} />
    </PromptInputProvider>
  );
}

function HeroInputInner({
  creating,
  onSubmit,
}: Parameters<typeof HeroInput>[0]) {
  const [scope, setScope] = useState<MaterialScope>("project");
  const [selectionError, setSelectionError] = useState("");
  const controller = usePromptInputController();
  const submitAttachments = async (
    text: string,
    attachments: typeof controller.attachments.files,
  ) => {
    const decision = attachmentSubmissionDecision(attachments.map((item) => ({
      status: item.uploadStatus ?? "selected",
      materialId: item.materialId,
      omitted: item.omitted ?? false,
    })));
    if (!decision.ready) throw new Error("failed attachments require retry, remove, or omit");
    const pending = attachments.filter((item) => !item.materialId && !item.omitted);
    if (pending.some((item) => !item.file)) throw new Error("chosen attachment bytes are unavailable");
    await onSubmit(
      text,
      pending.map((item) => item.file!),
      scope,
      decision.materialIds,
      (progress) => {
        const attachment = pending.find((item) => item.file === progress.file);
        if (attachment) controller.attachments.update(attachment.id, {
          uploadStatus: progress.status,
          materialId: progress.materialId,
          uploadError: progress.error,
        });
      },
    );
  };
  return (
    <PromptInput
        accept="text/*,application/pdf,application/json,application/xml,application/zip,application/vnd.*,application/octet-stream,image/*,audio/*"
        batchMaxBytes={500 * 1024 * 1024}
        maxFiles={10}
        maxFileSize={100 * 1024 * 1024}
        multiple
        className="[&>[data-slot=input-group]]:rounded-2xl [&>[data-slot=input-group]]:border-border [&>[data-slot=input-group]]:bg-card [&>[data-slot=input-group]]:shadow-lg [&>[data-slot=input-group]]:shadow-black/20 dark:[&>[data-slot=input-group]]:bg-card"
        onError={(error) => {
          setSelectionError({
            accept: copy.materials.policyType,
            max_file_size: copy.materials.policyFileSize,
            max_files: copy.materials.policyBatchCount,
            batch_size: copy.materials.policyBatchSize,
          }[error.code]);
        }}
        onSubmit={async (message) => {
          const text = message.text.trim();
          if (!text && message.files.length === 0) return;
          await submitAttachments(text, message.files);
        }}
      >
        {selectionError && <p role="alert" className="px-3 pt-2 text-xs text-destructive">{selectionError}</p>}
        <AttachmentTray
          onRetry={async (attachmentId) => {
            controller.attachments.update(attachmentId, {
              omitted: false,
              uploadError: undefined,
              uploadStatus: "selected",
            });
            await Promise.resolve();
            const text = controller.textInput.value.trim();
            const attachments = controller.attachments.files.map((item) => (
              item.id === attachmentId
                ? { ...item, omitted: false, uploadError: undefined, uploadStatus: "selected" as const }
                : item
            ));
            await submitAttachments(text, attachments).then(() => {
              controller.attachments.clear();
              controller.textInput.clear();
            }).catch(() => undefined);
          }}
        />
        <PromptInputTextarea
          placeholder={copy.landing.heroPlaceholder}
          disabled={creating}
          className="min-h-20"
        />
        <PromptInputFooter>
          <MaterialAddMenu scope={scope} onScopeChange={setScope} />
          <span className="hidden px-1 text-[11px] text-muted-foreground/70 sm:inline">
            {copy.research.composerHint}
          </span>
          <PromptInputSubmit className="rounded-full" disabled={creating} />
        </PromptInputFooter>
      </PromptInput>
  );
}

export default function LandingPage() {
  const router = useRouter();
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<CommissionDraft | null>(null);
  const [connection, setConnection] = useState<D18Connection>({
    status: "connecting",
    session: null,
  });
  const pendingLaunchResearchId = useRef<string | null>(null);

  const connectBackend = useCallback(() => {
    setConnection({ status: "connecting", session: null });
    void connectD18Session().then(setConnection);
  }, []);

  useEffect(() => {
    let current = true;
    void connectD18Session().then((next) => {
      if (current) setConnection(next);
    });
    return () => {
      current = false;
    };
  }, []);

  const launch = async (
    text: string,
    files: File[] = [],
    scope: MaterialScope = "project",
    completedMaterialIds: string[] = [],
    onProgress?: (progress: MaterialUploadProgress) => void,
  ) => {
    const compiled = compileComposerSubmission(text, []);
    const t = compiled.text;
    if (creating || connection.status !== "connected") return;
    setCreating(true);
    try {
      if (!pendingLaunchResearchId.current) {
        try {
          const session = await ensureD18Session();
          pendingLaunchResearchId.current = createResearchForNamespace(session.projectNamespace, t).researchId;
        } catch (error) {
          setConnection({ status: "unavailable", session: null });
          throw error;
        }
      }
      const researchId = pendingLaunchResearchId.current;
      let materialIds = completedMaterialIds;
      if (files.length > 0) {
        materialIds = [
          ...completedMaterialIds,
          ...await uploadMaterialFiles(researchId, files, scope, onProgress),
        ];
      }
      updateResearch(researchId, {
        pendingMaterialIds: materialIds,
        materials: materialIds.map((_materialId, index) => ({
          name: index < completedMaterialIds.length ? copy.materials.uploaded : files[index - completedMaterialIds.length]?.name ?? copy.materials.uploaded,
          note: copy.materials.uploaded,
        })),
      });
      pendingLaunchResearchId.current = null;
      router.push(`/research/${researchId}`);
    } finally {
      setCreating(false);
    }
  };

  const selectTask = (taskId: ResearchTaskId) => {
    setDraft(createCommissionDraft(taskId));
  };

  return (
    <div className="mx-auto flex min-h-dvh max-w-5xl flex-col px-6 pb-14">
      {/* 首屏 hero：垂直居中， eyebrow + 大标题 + 定位句 + 输入框 + 示例 chips */}
      <div className="flex min-h-[88dvh] flex-col items-center justify-center">
        <div className="w-full max-w-2xl space-y-8">
          <motion.div
            {...reveal}
            transition={{ duration: 0.4 }}
            className="space-y-4 text-center"
          >
            <p className={`${microLabel} text-muted-foreground`}>
              {copy.landing.eyebrow}
            </p>
            <h1 className="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
              {copy.agent.name}
            </h1>
            <p className="mx-auto max-w-md text-sm leading-7 text-muted-foreground">
              {copy.agent.tagline}
            </p>
          </motion.div>

          <motion.div {...reveal} transition={{ duration: 0.45, delay: 0.08 }}>
            <HeroInput
              creating={creating || connection.status !== "connected"}
              onSubmit={launch}
            />
            <div
              role="status"
              aria-live="polite"
              className="mt-3 flex items-center justify-center gap-2 font-mono text-[11px]"
            >
              <span
                aria-hidden="true"
                className={`size-1.5 rounded-full ${
                  connection.status === "connected"
                    ? "bg-chart-1"
                    : connection.status === "unavailable"
                      ? "bg-destructive"
                      : "animate-pulse bg-chart-5"
                }`}
              />
              <span
                className={
                  connection.status === "unavailable"
                    ? "text-destructive"
                    : "text-muted-foreground"
                }
              >
                {connection.status === "connected"
                  ? `${copy.projects.connectionConnected} · ${connection.session.projectNamespace} · ${connection.session.releaseId.slice(0, 8)}`
                  : connection.status === "unavailable"
                    ? copy.projects.connectionUnavailable
                    : copy.projects.connectionConnecting}
              </span>
              {connection.status === "unavailable" && (
                <button
                  type="button"
                  onClick={connectBackend}
                  className="text-muted-foreground underline-offset-4 hover:text-chart-1 hover:underline"
                >
                  {copy.projects.connectionRetry}
                </button>
              )}
            </div>
          </motion.div>

        </div>
      </div>

      <motion.section
        {...reveal}
        transition={{ duration: 0.45, delay: 0.16 }}
        className="mt-8"
      >
        <ResearchTaskLibrary
          selectedTaskId={draft?.taskId}
          onSelect={selectTask}
        />
        {draft && (
          <div className="mt-6 scroll-mt-6">
            <CommissionComposer
              draft={draft}
              onChange={setDraft}
              onSubmit={(text) => void launch(text)}
              disabled={creating || connection.status !== "connected"}
            />
          </div>
        )}
      </motion.section>

      <motion.section
        {...reveal}
        transition={{ duration: 0.45, delay: 0.24 }}
        className="mt-16"
      >
        <UsageGuide />
      </motion.section>

      {/* 边界 */}
      <motion.section
        {...reveal}
        transition={{ duration: 0.45, delay: 0.32 }}
        className="mt-16"
      >
        <p className={`${microLabel} text-muted-foreground`}>
          {copy.landing.principlesTitle}
        </p>
        <ul className="mt-4 space-y-2 border-t border-border pt-4">
          {copy.landing.principles.map((p) => (
            <li
              key={p}
              className="font-mono text-xs leading-6 text-muted-foreground"
            >
              <span className="mr-2 text-chart-1">›</span>
              {p}
            </li>
          ))}
        </ul>
      </motion.section>
    </div>
  );
}
