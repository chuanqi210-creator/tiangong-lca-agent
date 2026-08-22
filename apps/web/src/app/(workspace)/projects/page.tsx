"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Menu } from "lucide-react";
import { CommissionComposer } from "@/components/research/CommissionComposer";
import { ResearchTaskLibrary } from "@/components/research/ResearchTaskLibrary";
import {
  createCommissionDraft,
  type CommissionDraft,
} from "@/lib/commission-draft";
import { copy } from "@/lib/copy";
import { jobStatusBadge } from "@/lib/d18/map";
import {
  isTextFile,
  readMaterialFile,
  stagedMaterialsToFiles,
  type StagedMaterial,
} from "@/lib/d18/materials";
import { uploadMaterialFiles } from "@/lib/d18/material-client";
import {
  createResearchForNamespace,
  removeResearchCascade,
  updateResearch,
} from "@/lib/registry";
import {
  connectD18Session,
  type D18Connection,
} from "@/lib/session-client";
import { formatRelativeTime } from "@/lib/time";
import { useResearchEntries } from "@/lib/use-registry";
import { useAppShell } from "@/components/layout/AppShell";
import type { ResearchTaskId } from "@/lib/research-task-library";

const toneClass = {
  phos: "text-chart-1",
  amber: "text-chart-5",
  dim: "text-muted-foreground",
  bad: "text-destructive",
} as const;

const microLabel = "font-mono text-[11px] tracking-widest uppercase";
const ghostBtn =
  "font-mono text-[11px] text-muted-foreground underline-offset-4 transition-colors hover:text-chart-1 hover:underline disabled:opacity-30";

export default function ProjectsPage() {
  const router = useRouter();
  const { openSidebar } = useAppShell();
  const entries = useResearchEntries();
  const [mounted, setMounted] = useState(false);
  const [draft, setDraft] = useState<CommissionDraft>(() =>
    createCommissionDraft(),
  );
  const [materials, setMaterials] = useState<StagedMaterial[]>([]);
  const [materialOpen, setMaterialOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [materialError, setMaterialError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [connection, setConnection] = useState<D18Connection>({
    status: "connecting",
    session: null,
  });
  const fileRef = useRef<HTMLInputElement>(null);
  const pendingCreateResearchId = useRef<string | null>(null);

  const connectBackend = useCallback(() => {
    setConnection({ status: "connecting", session: null });
    void connectD18Session().then(setConnection);
  }, []);

  // localStorage 注册表仅客户端可读：挂载前不渲染列表，避免空态闪烁
  useEffect(() => {
    const timer = setTimeout(() => setMounted(true), 0);
    return () => clearTimeout(timer);
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

  const create = async (compiledObjective: string) => {
    const text = compiledObjective.trim();
    if (!text || creating || connection.status !== "connected") return;
    setCreating(true);
    try {
      if (!pendingCreateResearchId.current) {
        pendingCreateResearchId.current = createResearchForNamespace(
          connection.session.projectNamespace,
          text,
        ).researchId;
      }
      const researchId = pendingCreateResearchId.current;
      const files = stagedMaterialsToFiles(materials);
      const materialIds = files.length
        ? await uploadMaterialFiles(researchId, files, "project")
        : [];
      updateResearch(researchId, {
        pendingMaterialIds: materialIds,
        pendingSourceMaterialCount: 0,
        materials: materials.map((material) => ({
          name: material.name,
          note: copy.research.materialNoteObjective,
        })),
      });
      pendingCreateResearchId.current = null;
      router.push(`/research/${researchId}`);
    } catch {
      setMaterialError(copy.toast.submitFailed);
    } finally {
      setCreating(false);
    }
  };

  const selectTask = (taskId: ResearchTaskId) => {
    setDraft(createCommissionDraft(taskId));
  };

  const addPasted = () => {
    const text = pasteText.trim();
    if (!text) {
      setMaterialError(copy.projects.materialEmpty);
      return;
    }
    setMaterialError(null);
    setMaterials((prev) => [
      ...prev,
      { name: `粘贴材料 ${prev.length + 1}`, text },
    ]);
    setPasteText("");
  };

  const addFiles = async (files: FileList) => {
    for (const file of Array.from(files)) {
      if (!isTextFile(file)) {
        setMaterialError(`${copy.projects.materialFileUnsupported}${file.name}`);
        continue;
      }
      try {
        const material = await readMaterialFile(file);
        setMaterials((prev) => [...prev, material]);
        setMaterialError(null);
      } catch {
        setMaterialError(`${copy.projects.materialFileUnsupported}${file.name}`);
      }
    }
  };

  return (
    <div className="mx-auto flex min-h-dvh max-w-5xl flex-col px-6 py-14">
      <div className="flex items-center gap-3 border-b border-border pb-3">
        <button
          type="button"
          onClick={openSidebar}
          aria-label={copy.topbar.openSidebar}
          className="-ml-1 flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:hidden"
        >
          <Menu className="size-4" />
        </button>
        <p className={`${microLabel} flex-1 text-muted-foreground`}>
          {copy.projects.eyebrow}
        </p>
        <Link href="/" className={ghostBtn}>
          ‹ {copy.agent.name}
        </Link>
      </div>

      <h1 className="mt-10 text-2xl font-semibold tracking-tight text-foreground">
        {copy.projects.title}
      </h1>

      <div
        role="status"
        aria-live="polite"
        className="mt-3 flex items-center gap-2 font-mono text-[11px]"
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
          <button type="button" onClick={connectBackend} className={ghostBtn}>
            {copy.projects.connectionRetry}
          </button>
        )}
      </div>

      <div className="mt-8">
        <ResearchTaskLibrary
          selectedTaskId={draft.taskId}
          onSelect={selectTask}
        />
      </div>

      <div className="mt-6">
        <CommissionComposer
          draft={draft}
          onChange={setDraft}
          onSubmit={(text) => void create(text)}
          disabled={creating || connection.status !== "connected"}
          materialsSlot={
            <div>
              <button
                type="button"
                onClick={() => setMaterialOpen((value) => !value)}
                className={ghostBtn}
              >
                [{materialOpen ? "−" : "+"}] {copy.projects.addMaterial}
              </button>
              {materialOpen && (
                <div className="mt-2 rounded-md border border-border px-3 py-3">
                  <textarea
                    value={pasteText}
                    onChange={(event) => setPasteText(event.target.value)}
                    rows={4}
                    placeholder={copy.projects.materialPastePlaceholder}
                    className="w-full resize-y bg-transparent font-mono text-xs leading-5 text-foreground outline-none placeholder:text-muted-foreground/60"
                  />
                  <div className="mt-2 flex items-center gap-4 border-t border-border pt-2">
                    <button
                      type="button"
                      onClick={addPasted}
                      disabled={!pasteText.trim()}
                      className={ghostBtn}
                    >
                      {copy.projects.materialAdd} ↵
                    </button>
                    <button
                      type="button"
                      onClick={() => fileRef.current?.click()}
                      className={ghostBtn}
                    >
                      [+ {copy.projects.materialUpload}]
                    </button>
                    <input
                      ref={fileRef}
                      type="file"
                      multiple
                      accept=".txt,.md,.csv,.json,text/*,application/json"
                      className="hidden"
                      onChange={(event) => {
                        if (event.target.files?.length) {
                          void addFiles(event.target.files);
                        }
                        event.target.value = "";
                      }}
                    />
                  </div>
                  {materialError && (
                    <p className="mt-2 font-mono text-[11px] text-destructive">
                      {materialError}
                    </p>
                  )}
                </div>
              )}
              {materials.length > 0 && (
                <ul className="mt-2 border-t border-border">
                  {materials.map((material, index) => (
                    <li
                      key={`${material.name}-${index}`}
                      className="group flex items-baseline gap-3 border-b border-border py-1.5"
                    >
                      <span
                        className={`${microLabel} shrink-0 text-muted-foreground/70`}
                      >
                        {copy.projects.materialStaged}
                      </span>
                      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">
                        {material.name}
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          setMaterials((previous) =>
                            previous.filter((_, itemIndex) => itemIndex !== index),
                          )
                        }
                        className="font-mono text-[11px] text-muted-foreground/70 opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                      >
                        [×]
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          }
        />
      </div>

      {/* 项目列表 */}
      {!mounted ? null : entries.length === 0 ? (
        <div className="mt-16 border-t border-border pt-8">
          <p className="text-sm text-muted-foreground">
            {copy.projects.emptyTitle}
          </p>
          <p className="mt-1 text-sm leading-6 text-muted-foreground/70">
            {copy.projects.emptyBody}
          </p>
        </div>
      ) : (
        <ul className="mt-16 border-t border-border">
          {entries.map((e) => {
            const badge = e.status ? jobStatusBadge[e.status] : null;
            return (
              <motion.li
                key={e.researchId}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25 }}
                className="group flex items-baseline gap-4 border-b border-border py-3.5"
              >
                <span
                  className={`${microLabel} w-16 shrink-0 ${
                    badge ? toneClass[badge.tone] : "text-muted-foreground/70"
                  }`}
                >
                  {badge ? badge.label : "新建"}
                </span>
                <Link
                  href={`/research/${e.researchId}`}
                  className="min-w-0 flex-1"
                >
                  <span className="block truncate text-sm text-foreground transition-colors group-hover:text-chart-1">
                    {e.title}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-muted-foreground/70 tabular-nums">
                    {copy.projects.updatedAt} {formatRelativeTime(e.updatedAt)}
                  </span>
                  <span className="mt-0.5 block font-mono text-[10px] text-muted-foreground/60">
                    {copy.projects.openMaterials}
                  </span>
                </Link>
                <button
                  onClick={() => removeResearchCascade(e.researchId)}
                  aria-label={copy.projects.remove}
                  className="font-mono text-[11px] text-muted-foreground/70 opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                >
                  [×]
                </button>
                <Link
                  href={`/research/${e.researchId}`}
                  className="font-mono text-sm text-muted-foreground/70 transition-colors group-hover:text-chart-1"
                >
                  →
                </Link>
              </motion.li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
