import {
  ClipboardList,
  Database,
  FileCheck2,
  Orbit,
  type LucideIcon,
} from "lucide-react";
import { copy } from "@/lib/copy";

const stageIcons: readonly LucideIcon[] = [
  ClipboardList,
  Database,
  Orbit,
  FileCheck2,
];
const microLabel = "font-mono text-[11px] tracking-widest uppercase";

export function UsageGuide() {
  return (
    <section aria-labelledby="usage-guide-title">
      <p className={`${microLabel} text-muted-foreground`}>
        {copy.usageGuide.eyebrow}
      </p>
      <div className="mt-2 max-w-2xl">
        <h2
          id="usage-guide-title"
          className="text-xl font-semibold tracking-tight text-foreground"
        >
          {copy.usageGuide.title}
        </h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          {copy.usageGuide.body}
        </p>
      </div>

      <ol className="mt-6 grid border-l border-t border-border md:grid-cols-2 xl:grid-cols-4">
        {copy.usageGuide.stages.map((stage, index) => {
          const Icon = stageIcons[index];
          return (
            <li
              key={stage.title}
              className="border-b border-r border-border p-4 sm:p-5"
            >
              <div className="flex items-center justify-between gap-3">
                <Icon aria-hidden="true" className="size-4 text-chart-1" />
                <span
                  className={`${microLabel} tabular-nums text-muted-foreground/60`}
                >
                  {String(index + 1).padStart(2, "0")}
                </span>
              </div>
              <h3 className="mt-5 text-sm font-medium text-foreground">
                {stage.title}
              </h3>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                {stage.body}
              </p>
            </li>
          );
        })}
      </ol>

      <div className="mt-8">
        <h3 className={`${microLabel} text-muted-foreground`}>
          {copy.usageGuide.audiencesTitle}
        </h3>
        <div className="mt-3 divide-y divide-border border-y border-border">
          {copy.usageGuide.audiences.map((audience, index) => (
            <details key={audience.id} open={index === 0} className="group">
              <summary className="cursor-pointer list-none py-3 text-sm font-medium text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <span className="mr-3 font-mono text-[11px] text-chart-1 group-open:hidden">
                  [+]
                </span>
                <span className="mr-3 hidden font-mono text-[11px] text-chart-1 group-open:inline">
                  [−]
                </span>
                {audience.title}
              </summary>
              <p className="pb-4 pl-9 text-sm leading-6 text-muted-foreground">
                {audience.body}
              </p>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}
