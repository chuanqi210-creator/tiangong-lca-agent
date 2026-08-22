"use client";

import {
  BookOpen,
  Calculator,
  Database,
  FileCheck2,
  GitCompareArrows,
  ListChecks,
  ScanSearch,
  Target,
  Waypoints,
  type LucideIcon,
} from "lucide-react";
import { copy } from "@/lib/copy";
import {
  taskLibrarySections,
  type ResearchOutcomeKind,
  type ResearchTaskId,
} from "@/lib/research-task-library";
import { cn } from "@/lib/utils";

const iconByOutcome: Record<ResearchOutcomeKind, LucideIcon> = {
  goal_and_scope: Target,
  official_data: Database,
  inventory_gap: ListChecks,
  calculation: Calculator,
  comparison_uncertainty: GitCompareArrows,
  interpretation: Waypoints,
  review: FileCheck2,
  enterprise_decision: ScanSearch,
  learning: BookOpen,
};

const microLabel = "font-mono text-[11px] tracking-widest uppercase";

export function ResearchTaskLibrary({
  selectedTaskId,
  onSelect,
  compact = false,
}: {
  selectedTaskId?: ResearchTaskId;
  onSelect: (taskId: ResearchTaskId) => void;
  compact?: boolean;
}) {
  return (
    <section aria-labelledby="research-task-library-title">
      <p className={`${microLabel} text-muted-foreground`}>
        {copy.taskLibrary.eyebrow}
      </p>
      <div className="mt-2 max-w-2xl">
        <h2
          id="research-task-library-title"
          className="text-xl font-semibold tracking-tight text-foreground"
        >
          {copy.taskLibrary.title}
        </h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          {copy.taskLibrary.body}
        </p>
      </div>

      <div className="mt-6 space-y-8">
        {taskLibrarySections().map((section) => (
          <section
            key={section.id}
            aria-labelledby={`research-task-section-${section.id}`}
          >
            <h3
              id={`research-task-section-${section.id}`}
              className={`${microLabel} text-muted-foreground/80`}
            >
              {section.id === "professional"
                ? copy.taskLibrary.professionalGroup
                : copy.taskLibrary.learningGroup}
            </h3>
            <ul
              className={cn(
                "mt-3 grid gap-3",
                compact ? "grid-cols-1" : "md:grid-cols-2",
              )}
            >
              {section.tasks.map((task) => {
                const selected = selectedTaskId === task.id;
                const Icon = iconByOutcome[task.outcomeKind];
                const detailId = `research-task-${task.id}-detail`;
                return (
                  <li
                    key={task.id}
                    className={cn(
                      "rounded-xl border bg-card/40 transition-colors",
                      selected
                        ? "border-chart-1/60 bg-card"
                        : "border-border hover:border-muted-foreground/40",
                    )}
                  >
                    <button
                      type="button"
                      aria-expanded={selected}
                      aria-controls={detailId}
                      onClick={() => onSelect(task.id as ResearchTaskId)}
                      className="w-full rounded-xl px-4 py-4 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                    >
                      <span className="flex items-start gap-3">
                        <Icon
                          aria-hidden="true"
                          className={cn(
                            "mt-0.5 size-4 shrink-0",
                            selected ? "text-chart-1" : "text-muted-foreground",
                          )}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-medium text-foreground">
                            {task.title}
                          </span>
                          <span className="mt-1 block text-sm leading-6 text-muted-foreground">
                            {task.summary}
                          </span>
                        </span>
                      </span>
                      <span className="mt-3 flex flex-wrap gap-1.5 pl-7">
                        {task.audiences.map((audience) => (
                          <span
                            key={audience}
                            className="rounded-full border border-border px-2 py-0.5 font-mono text-[10px] text-muted-foreground"
                          >
                            {copy.taskLibrary.audiences[audience]}
                          </span>
                        ))}
                      </span>
                    </button>

                    {selected && (
                      <div
                        id={detailId}
                        className="border-t border-border px-4 pb-4 pt-3"
                      >
                        <TaskDetail
                          label={copy.taskLibrary.useWhen}
                          lines={[task.useWhen]}
                        />
                        <TaskDetail
                          label={copy.taskLibrary.minimumInput}
                          lines={task.minimumInput}
                        />
                        <TaskDetail
                          label={copy.taskLibrary.agentWork}
                          lines={task.agentWork}
                        />
                        <TaskDetail
                          label={copy.taskLibrary.deliverables}
                          lines={task.deliverables}
                        />
                        <TaskDetail
                          label={copy.taskLibrary.boundary}
                          lines={[task.boundary]}
                        />
                        <p className="mt-4 font-mono text-[11px] text-chart-1">
                          {copy.taskLibrary.editDraft} ↓
                        </p>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </section>
  );
}

function TaskDetail({ label, lines }: { label: string; lines: readonly string[] }) {
  return (
    <div className="mt-3 first:mt-0">
      <p className={`${microLabel} text-muted-foreground/70`}>{label}</p>
      <ul className="mt-1 space-y-1 text-sm leading-6 text-muted-foreground">
        {lines.map((line) => (
          <li key={line} className="flex gap-2">
            <span aria-hidden="true" className="text-chart-1">
              ›
            </span>
            <span>{line}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
