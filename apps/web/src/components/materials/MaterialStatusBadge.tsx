import { copy } from "@/lib/copy";

type Axis = "transfer" | "processing" | "scientific" | "usage" | "permission";

export function MaterialStatusBadge({ axis, value }: { axis: Axis; value: string | boolean }) {
  const normalized = String(value);
  const label = copy.materialLibrary.status[axis][normalized] ?? normalized;
  const tone =
    normalized === "failed" || normalized === "unsupported"
      ? "border-destructive/40 text-destructive"
      : normalized === "ready" || normalized === "accepted_project_input" || normalized === "true"
        ? "border-chart-1/40 text-chart-1"
        : "border-border text-muted-foreground";
  return (
    <span className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${tone}`}>
      {label}
    </span>
  );
}
