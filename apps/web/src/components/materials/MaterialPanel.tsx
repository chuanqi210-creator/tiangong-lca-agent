"use client";

import { useState } from "react";
import { copy } from "@/lib/copy";
import {
  addProjectMaterialVersion,
  reuseProjectOutput,
} from "@/lib/d18/material-client";
import { useMaterials } from "@/lib/use-materials";
import { MaterialDetail } from "./MaterialDetail";
import { MaterialStatusBadge } from "./MaterialStatusBadge";

export function MaterialPanel({
  researchId,
  fetcher = globalThis.fetch,
}: {
  researchId: string;
  fetcher?: typeof fetch;
}) {
  const library = useMaterials(researchId, fetcher);
  const [outputArtifactId, setOutputArtifactId] = useState("");
  const directories = Array.from(new Set(library.materials.map((row) => row.directory || "")));

  return (
    <section aria-label={copy.materialLibrary.title} className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-mono text-[11px] tracking-widest text-muted-foreground uppercase">
          {copy.materialLibrary.title}
        </h3>
        <button
          type="button"
          onClick={() => void library.refresh()}
          className="font-mono text-[10px] text-muted-foreground underline-offset-4 hover:underline"
        >
          {copy.materialLibrary.refresh}
        </button>
      </div>

      <div className="grid grid-cols-2 gap-1">
        <label className="col-span-2">
          <span className="sr-only">{copy.materialLibrary.search}</span>
          <input
            type="search"
            value={library.filters.query ?? ""}
            onChange={(event) =>
              library.setFilters((prior) => ({ ...prior, query: event.target.value || undefined }))
            }
            placeholder={copy.materialLibrary.search}
            className="w-full rounded border border-border bg-transparent px-2 py-1.5 text-xs outline-none focus:border-chart-1"
          />
        </label>
        <select
          aria-label={copy.materialLibrary.typeFilter}
          value={library.filters.typeFamily ?? ""}
          onChange={(event) =>
            library.setFilters((prior) => ({ ...prior, typeFamily: event.target.value || undefined }))
          }
          className="rounded border border-border bg-background px-1 py-1 text-[11px]"
        >
          <option value="">{copy.materialLibrary.allTypes}</option>
          <option value="document">document</option>
          <option value="table">table</option>
          <option value="image">image</option>
          <option value="audio">audio</option>
          <option value="package">package</option>
        </select>
        <select
          aria-label={copy.materialLibrary.scientificFilter}
          value={library.filters.scientificStatus ?? ""}
          onChange={(event) =>
            library.setFilters((prior) => ({ ...prior, scientificStatus: event.target.value || undefined }))
          }
          className="rounded border border-border bg-background px-1 py-1 text-[11px]"
        >
          <option value="">{copy.materialLibrary.allScientific}</option>
          <option value="candidate_only">{copy.materialLibrary.status.scientific.candidate_only}</option>
          <option value="accepted_project_input">{copy.materialLibrary.status.scientific.accepted_project_input}</option>
        </select>
        <select
          aria-label={copy.materialLibrary.processingFilter}
          value={library.filters.processingStatus ?? ""}
          onChange={(event) =>
            library.setFilters((prior) => ({ ...prior, processingStatus: event.target.value || undefined }))
          }
          className="rounded border border-border bg-background px-1 py-1 text-[11px]"
        >
          <option value="">{copy.materialLibrary.allProcessing}</option>
          <option value="ready">{copy.materialLibrary.status.processing.ready}</option>
          <option value="pending_security">{copy.materialLibrary.status.processing.pending_security}</option>
          <option value="failed">{copy.materialLibrary.status.processing.failed}</option>
        </select>
        <label>
          <span className="sr-only">{copy.materialLibrary.tagFilter}</span>
          <input
            value={library.filters.tag ?? ""}
            onChange={(event) =>
              library.setFilters((prior) => ({ ...prior, tag: event.target.value || undefined }))
            }
            placeholder={copy.materialLibrary.tagFilter}
            className="w-full rounded border border-border bg-transparent px-2 py-1 text-[11px]"
          />
        </label>
        <label>
          <span className="sr-only">{copy.materialLibrary.directoryFilter}</span>
          <input
            value={library.filters.directory ?? ""}
            onChange={(event) =>
              library.setFilters((prior) => ({ ...prior, directory: event.target.value || undefined }))
            }
            placeholder={copy.materialLibrary.directoryFilter}
            className="w-full rounded border border-border bg-transparent px-2 py-1 text-[11px]"
          />
        </label>
        <select
          aria-label={copy.materialLibrary.scopeFilter}
          value={library.filters.scope ?? ""}
          onChange={(event) =>
            library.setFilters((prior) => ({
              ...prior,
              scope: (event.target.value || undefined) as "turn" | "project" | undefined,
            }))
          }
          className="rounded border border-border bg-background px-1 py-1 text-[11px]"
        >
          <option value="">{copy.materialLibrary.allScopes}</option>
          <option value="turn">{copy.materialLibrary.scopeTurn}</option>
          <option value="project">{copy.materialLibrary.scopeProject}</option>
        </select>
        <label className="col-span-2 flex items-center gap-2 text-[11px] text-muted-foreground">
          <input
            type="checkbox"
            checked={library.filters.deleted === "all"}
            onChange={(event) =>
              library.setFilters((prior) => ({ ...prior, deleted: event.target.checked ? "all" : "false" }))
            }
          />
          {copy.materialLibrary.showWithdrawn}
        </label>
      </div>

      {library.error && <p role="alert" className="text-xs text-destructive">{copy.materialLibrary.unavailable}</p>}
      {library.loading ? (
        <p className="text-xs text-muted-foreground">{copy.materialLibrary.loading}</p>
      ) : library.materials.length === 0 ? (
        <p className="text-xs text-muted-foreground">{copy.materialLibrary.empty}</p>
      ) : (
        <div className="space-y-3">
          {directories.map((directory) => (
            <section key={directory || "root"} aria-label={directory || copy.materialLibrary.rootDirectory}>
              <h4 className="border-b border-border pb-1 font-mono text-[10px] text-muted-foreground">
                {directory || copy.materialLibrary.rootDirectory}
              </h4>
              <ul className="divide-y divide-border">
          {library.materials.filter((material) => material.directory === directory).map((material) => (
            <li key={material.material_id} className="py-2">
              <button
                type="button"
                onClick={() => void library.select(material.material_id)}
                aria-expanded={library.selectedId === material.material_id}
                className="w-full text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-chart-1"
              >
                <span className="block truncate text-xs text-foreground">{material.display_name}</span>
                <span className="mt-1 flex flex-wrap gap-1">
                  <MaterialStatusBadge axis="transfer" value={material.transfer_status} />
                  <MaterialStatusBadge axis="processing" value={material.current_revision.processing_status} />
                  <MaterialStatusBadge axis="scientific" value={material.current_revision.scientific_status} />
                  <MaterialStatusBadge axis="usage" value={Boolean(material.current_revision.used_in_answer)} />
                </span>
                {(material.directory || material.tags.length > 0) && (
                  <span className="mt-1 block truncate font-mono text-[10px] text-muted-foreground">
                    {[material.directory, ...material.tags].filter(Boolean).join(" / ")}
                  </span>
                )}
              </button>
              {material.name_conflict && (
                <div role="group" aria-label={`${copy.materialLibrary.conflictTitle}: ${material.display_name}`} className="mt-2 flex flex-wrap gap-1">
                  {material.name_conflict.existing_material_ids.map((targetId) => {
                    const target = library.materials.find((row) => row.material_id === targetId);
                    if (!target) return null;
                    return (
                      <button
                        key={targetId}
                        type="button"
                        onClick={() => void library.command(`conflict:${material.material_id}`, () =>
                          addProjectMaterialVersion(
                            researchId,
                            targetId,
                            material.material_id,
                            `version-${targetId}-${material.material_id}`,
                            fetcher,
                          ))}
                        className="rounded border border-chart-4/50 px-2 py-1 text-[10px] text-chart-4"
                      >
                        {copy.materialLibrary.replaceVersion}: {target.display_name}
                      </button>
                    );
                  })}
                  <span className="rounded border border-border px-2 py-1 text-[10px] text-muted-foreground">
                    {copy.materialLibrary.keepSeparate}
                  </span>
                </div>
              )}
            </li>
          ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      {library.detail && (
        <>
          <MaterialDetail
            researchId={researchId}
            detail={library.detail}
            command={library.command}
            fetcher={fetcher}
          />
        </>
      )}

      <form
        className="border-t border-border pt-3"
        onSubmit={(event) => {
          event.preventDefault();
          const value = outputArtifactId.trim();
          if (!value) return;
          void library
            .command("reuse", () =>
              reuseProjectOutput(
                researchId,
                value,
                "project",
                `reuse-${value}`,
                fetcher,
              ),
            )
            .then(() => setOutputArtifactId(""));
        }}
      >
        <label className="block font-mono text-[10px] text-muted-foreground">
          {copy.materialLibrary.reuseOutput}
          <span className="mt-1 flex gap-1">
            <input
              value={outputArtifactId}
              onChange={(event) => setOutputArtifactId(event.target.value)}
              placeholder={copy.materialLibrary.outputArtifactId}
              className="min-w-0 flex-1 rounded border border-border bg-transparent px-2 py-1 text-xs outline-none focus:border-chart-1"
            />
            <button type="submit" className="rounded border border-border px-2 py-1 text-[10px]">
              {copy.materialLibrary.reuse}
            </button>
          </span>
        </label>
      </form>
    </section>
  );
}
