"use client";

import {
  PromptInputActionAddAttachments,
  PromptInputActionAddDirectory,
} from "@/components/ai-elements/prompt-input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { copy } from "@/lib/copy";
import type { MaterialScope } from "@/lib/d18/material-types";
import { PaperclipIcon } from "lucide-react";

export function MaterialAddMenu({ scope, onScopeChange }: { scope: MaterialScope; onScopeChange: (scope: MaterialScope) => void }) {
  return (
    <div className="flex items-center gap-1">
      <DropdownMenu>
        <DropdownMenuTrigger className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={copy.materials.add}>
          <PaperclipIcon className="size-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <PromptInputActionAddAttachments label={copy.materials.chooseFiles} />
          <PromptInputActionAddDirectory label={copy.materials.chooseFolder} />
        </DropdownMenuContent>
      </DropdownMenu>
      <button
        type="button"
        aria-label={`${copy.materials.scope}：${scope === "turn" ? copy.materials.scopeTurn : copy.materials.scopeProject}`}
        className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
        onClick={() => onScopeChange(scope === "turn" ? "project" : "turn")}
      >
        {scope === "turn" ? copy.materials.scopeTurn : copy.materials.scopeProject}
      </button>
    </div>
  );
}
