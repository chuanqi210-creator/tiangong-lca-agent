"use client";

import { useMemo, useState, type ReactNode } from "react";
import { ArrowUpIcon, ChevronDown, ClipboardCheck } from "lucide-react";
import { copy } from "@/lib/copy";
import {
  compileCommissionObjective,
  commissionHasObjective,
  type CommissionDraft,
  type CommissionFieldState,
  type ProfessionalFieldKey,
} from "@/lib/commission-draft";
import { cn } from "@/lib/utils";

const fieldKeys: readonly ProfessionalFieldKey[] = [
  "intendedUse",
  "functionalUnit",
  "systemBoundary",
  "geography",
  "timePeriod",
  "comparisonBasis",
  "lciaMethod",
  "dataPriority",
  "modellingRules",
  "uncertainty",
];

const stateOptions: readonly {
  value: CommissionFieldState;
  label: string;
}[] = [
  { value: "supplied", label: copy.commission.supplied },
  {
    value: "agent_may_determine",
    label: copy.commission.agentMayDetermine,
  },
  { value: "unknown", label: copy.commission.unknown },
  { value: "not_applicable", label: copy.commission.notApplicable },
];

const inputClass =
  "w-full rounded-lg border border-border bg-background/50 px-3 py-2.5 text-sm text-foreground outline-none transition-colors placeholder:text-muted-foreground/55 focus:border-ring disabled:cursor-not-allowed disabled:opacity-40";
const microLabel = "font-mono text-[11px] tracking-widest uppercase";

export function CommissionComposer({
  draft,
  onChange,
  onSubmit,
  disabled = false,
  materialsSlot,
}: {
  draft: CommissionDraft;
  onChange: (next: CommissionDraft) => void;
  onSubmit: (compiledObjective: string) => void;
  disabled?: boolean;
  materialsSlot?: ReactNode;
}) {
  const [professionalOpen, setProfessionalOpen] = useState(false);
  const compiled = useMemo(() => {
    if (!commissionHasObjective(draft)) return null;
    try {
      return compileCommissionObjective(draft);
    } catch {
      return null;
    }
  }, [draft]);

  const setTopLevel = (
    key: "objective" | "studyObject" | "deliverable",
    value: string,
  ) => onChange({ ...draft, [key]: value });

  const setProfessional = (
    key: ProfessionalFieldKey,
    patch: { state?: CommissionFieldState; value?: string },
  ) =>
    onChange({
      ...draft,
      professional: {
        ...draft.professional,
        [key]: { ...draft.professional[key], ...patch },
      },
    });

  return (
    <form
      className="rounded-2xl border border-border bg-card/60 p-4 shadow-lg shadow-black/15 sm:p-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (compiled && !disabled) onSubmit(compiled);
      }}
    >
      <div className="grid gap-4 md:grid-cols-2">
        <label className="md:col-span-2">
          <span className={`${microLabel} text-muted-foreground`}>
            {copy.commission.objective}
          </span>
          <textarea
            value={draft.objective}
            onChange={(event) => setTopLevel("objective", event.target.value)}
            rows={4}
            placeholder={copy.commission.objectivePlaceholder}
            className={cn(inputClass, "mt-2 resize-y leading-6")}
          />
        </label>
        <label>
          <span className={`${microLabel} text-muted-foreground`}>
            {copy.commission.studyObject}
          </span>
          <input
            value={draft.studyObject}
            onChange={(event) => setTopLevel("studyObject", event.target.value)}
            placeholder={copy.commission.studyObjectPlaceholder}
            className={cn(inputClass, "mt-2")}
          />
        </label>
        <label>
          <span className={`${microLabel} text-muted-foreground`}>
            {copy.commission.deliverable}
          </span>
          <input
            value={draft.deliverable}
            onChange={(event) => setTopLevel("deliverable", event.target.value)}
            placeholder={copy.commission.deliverablePlaceholder}
            className={cn(inputClass, "mt-2")}
          />
        </label>
      </div>

      <button
        type="button"
        aria-expanded={professionalOpen}
        aria-controls="commission-professional-fields"
        onClick={() => setProfessionalOpen((value) => !value)}
        className="mt-5 flex w-full items-center gap-3 border-t border-border pt-4 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ChevronDown
          aria-hidden="true"
          className={cn(
            "size-4 shrink-0 text-chart-1 transition-transform",
            professionalOpen && "rotate-180",
          )}
        />
        <span>
          <span className="block text-sm font-medium text-foreground">
            {copy.commission.professionalDetails}
          </span>
          <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
            {copy.commission.professionalHelp}
          </span>
        </span>
      </button>

      {professionalOpen && (
        <div
          id="commission-professional-fields"
          className="mt-4 grid gap-4 border-t border-border pt-4 md:grid-cols-2"
        >
          {fieldKeys.map((key) => {
            const field = draft.professional[key];
            const content = copy.commission.fields[key];
            return (
              <div key={key} className="rounded-lg border border-border p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <label
                    htmlFor={`commission-${key}-value`}
                    className="text-sm font-medium text-foreground"
                  >
                    {content.label}
                  </label>
                  <select
                    aria-label={`${content.label}填写状态`}
                    value={field.state}
                    onChange={(event) =>
                      setProfessional(key, {
                        state: event.target.value as CommissionFieldState,
                      })
                    }
                    className="rounded-md border border-border bg-background px-2 py-1 font-mono text-[11px] text-muted-foreground outline-none focus:border-ring"
                  >
                    {stateOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {content.help}
                </p>
                <input
                  id={`commission-${key}-value`}
                  value={field.value}
                  disabled={field.state !== "supplied"}
                  onChange={(event) =>
                    setProfessional(key, { value: event.target.value })
                  }
                  placeholder={
                    field.state === "supplied"
                      ? `填写${content.label}`
                      : stateOptions.find((option) => option.value === field.state)
                          ?.label
                  }
                  className={cn(inputClass, "mt-3")}
                />
              </div>
            );
          })}
        </div>
      )}

      {materialsSlot && (
        <div className="mt-5 border-t border-border pt-4">{materialsSlot}</div>
      )}

      <div className="mt-5 border-t border-border pt-4">
        <div className="flex items-center gap-2">
          <ClipboardCheck aria-hidden="true" className="size-4 text-chart-1" />
          <p className={`${microLabel} text-muted-foreground`}>
            {copy.commission.reviewTitle}
          </p>
        </div>
        <pre className="mt-2 whitespace-pre-wrap font-sans text-sm leading-6 text-muted-foreground">
          {compiled ?? copy.commission.reviewEmpty}
        </pre>
      </div>

      <div className="mt-5 flex justify-end">
        <button
          type="submit"
          disabled={!compiled || disabled}
          className="inline-flex items-center gap-2 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-opacity disabled:opacity-30"
        >
          {copy.commission.submit}
          <ArrowUpIcon aria-hidden="true" className="size-4" />
        </button>
      </div>
    </form>
  );
}
