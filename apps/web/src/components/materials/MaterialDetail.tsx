"use client";

import { copy } from "@/lib/copy";
import {
  restoreProjectMaterial,
  updateProjectMaterialScope,
  withdrawProjectMaterial,
  type MaterialDetailResponse,
} from "@/lib/d18/material-client";
import { MaterialPreview } from "./MaterialPreview";
import { MaterialStatusBadge } from "./MaterialStatusBadge";

function commandKey(action: string, materialId: string) {
  return `material-${action}-${materialId}-${Date.now()}`;
}

export function MaterialDetail({
  researchId,
  detail,
  command,
  fetcher = globalThis.fetch,
}: {
  researchId: string;
  detail: MaterialDetailResponse;
  command: <T>(key: string, mutation: () => Promise<T>) => Promise<T>;
  fetcher?: typeof fetch;
}) {
  const material = detail.material;
  return (
    <section aria-label={`${material.display_name} ${copy.materialLibrary.detail}`} className="space-y-3 border-t border-border pt-3">
      <div className="flex flex-wrap items-center gap-1">
        <MaterialStatusBadge axis="processing" value={detail.current_revision.processing_status} />
        <MaterialStatusBadge axis="scientific" value={detail.current_revision.scientific_status} />
        <MaterialStatusBadge axis="usage" value={Boolean(detail.current_revision.used_in_answer)} />
        <MaterialStatusBadge axis="permission" value={Boolean(detail.current_revision.allowed_in_project)} />
      </div>
      <div className="flex flex-wrap gap-2 font-mono text-[10px]">
        <button
          type="button"
          className="underline underline-offset-4"
          onClick={() =>
            void command("scope", () =>
              updateProjectMaterialScope(
                researchId,
                material.material_id,
                material.scope === "project" ? "turn" : "project",
                commandKey("scope", material.material_id),
                fetcher,
              ),
            )
          }
        >
          {material.scope === "project" ? copy.materialLibrary.scopeTurn : copy.materialLibrary.scopeProject}
        </button>
        {material.deleted_at ? (
          <button
            type="button"
            className="text-chart-1 underline underline-offset-4"
            onClick={() =>
              void command("restore", () =>
                restoreProjectMaterial(
                  researchId,
                  material.material_id,
                  commandKey("restore", material.material_id),
                  fetcher,
                ),
              )
            }
          >
            {copy.materialLibrary.restore}
          </button>
        ) : (
          <button
            type="button"
            className="text-destructive underline underline-offset-4"
            onClick={() =>
              void command("delete", () =>
                withdrawProjectMaterial(
                  researchId,
                  material.material_id,
                  commandKey("delete", material.material_id),
                  fetcher,
                ),
              )
            }
          >
            {copy.materialLibrary.withdraw}
          </button>
        )}
        {!material.deleted_at && (
          <a
            href={`/api/d18/research/${encodeURIComponent(researchId)}/materials/${encodeURIComponent(material.material_id)}/download`}
            className="underline underline-offset-4"
          >
            {copy.materialLibrary.download}
          </a>
        )}
      </div>
      {!material.deleted_at && <MaterialPreview researchId={researchId} materialId={material.material_id} fetcher={fetcher} />}
      <div>
        <p className="font-mono text-[10px] text-muted-foreground">{copy.materialLibrary.versions}</p>
        <ol className="mt-1 space-y-1">
          {detail.revisions.map((revision) => (
            <li key={revision.revision_id} className="flex items-center justify-between gap-2 text-xs">
              <span>{copy.materialLibrary.versionPrefix}{revision.revision_number}</span>
              <MaterialStatusBadge axis="processing" value={revision.processing_status} />
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
