"use client";

/**
 * 会话侧栏：数据源为 localStorage 注册表（use-registry 订阅刷新）。
 * 分组「进行中 / 最近」，每项带 job 状态图标与 hover/右键菜单
 * （打开 / 重命名 / 删除）。结构参考 palot sidebar.tsx，状态库换为 React。
 */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleSlash,
  CircleX,
  Ellipsis,
  ExternalLink,
  LoaderCircle,
  Pencil,
  Plus,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { copy } from "@/lib/copy";
import type { JobStatus } from "@/lib/d18/types";
import { removeResearchCascade, renameResearch, type ResearchEntry } from "@/lib/registry";
import { formatRelativeTime } from "@/lib/time";
import { useResearchEntries } from "@/lib/use-registry";
import { cn } from "@/lib/utils";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/** job 状态 → 图标与 token 色（全部走语义 token，不写死色值） */
const STATUS_ICON: Record<
  JobStatus,
  { icon: typeof LoaderCircle; className: string; spin?: boolean }
> = {
  queued: { icon: LoaderCircle, className: "text-chart-1", spin: true },
  leased: { icon: LoaderCircle, className: "text-chart-1", spin: true },
  running: { icon: LoaderCircle, className: "text-chart-1", spin: true },
  waiting_for_input: { icon: CircleHelp, className: "text-chart-5" },
  cancelling: {
    icon: LoaderCircle,
    className: "text-muted-foreground",
    spin: true,
  },
  completed: { icon: CircleCheck, className: "text-chart-1" },
  bounded: { icon: TriangleAlert, className: "text-chart-5" },
  failed: { icon: CircleX, className: "text-destructive" },
  cancelled: { icon: CircleSlash, className: "text-muted-foreground" },
};

const NEW_ENTRY = {
  icon: CircleDashed,
  className: "text-muted-foreground",
  spin: false,
};

function isActiveStatus(status: JobStatus | null): boolean {
  return (
    status === "queued" ||
    status === "leased" ||
    status === "running" ||
    status === "waiting_for_input" ||
    status === "cancelling"
  );
}

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const entries = useResearchEntries();
  const [pendingDelete, setPendingDelete] = useState<ResearchEntry | null>(null);

  const currentId = pathname.startsWith("/research/")
    ? pathname.split("/")[2]
    : null;

  const active = entries.filter((e) => isActiveStatus(e.status));
  const recent = entries.filter((e) => !isActiveStatus(e.status));

  const confirmDelete = () => {
    if (!pendingDelete) return;
    removeResearchCascade(pendingDelete.researchId);
    if (currentId === pendingDelete.researchId) {
      router.push("/projects");
    }
    setPendingDelete(null);
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-sidebar text-sidebar-foreground">
      {/* 产品区 + 新建研究 */}
      <div className="shrink-0 border-b border-sidebar-border px-3 py-3">
        <Link
          href="/"
          className="block truncate text-sm font-semibold tracking-tight"
        >
          {copy.agent.name}
        </Link>
        <Link
          href="/projects"
          className="mt-2.5 flex items-center gap-1.5 rounded-md border border-sidebar-border px-2.5 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        >
          <Plus className="size-4" />
          {copy.sidebar.newResearch}
        </Link>
      </div>

      {/* 会话分组 */}
      <nav className="scrollbar-auto min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {entries.length === 0 ? (
          <p className="px-2 py-6 text-center text-xs text-muted-foreground">
            {copy.sidebar.empty}
          </p>
        ) : (
          <>
            {active.length > 0 && (
              <SidebarGroup
                label={copy.sidebar.groupActive}
                entries={active}
                currentId={currentId}
                onDelete={setPendingDelete}
              />
            )}
            {recent.length > 0 && (
              <SidebarGroup
                label={copy.sidebar.groupRecent}
                entries={recent}
                currentId={currentId}
                onDelete={setPendingDelete}
              />
            )}
          </>
        )}
      </nav>

      {/* 删除确认 */}
      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
      >
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>{copy.sidebar.removeConfirmTitle}</DialogTitle>
            <DialogDescription>
              {pendingDelete?.title}
              <br />
              {copy.sidebar.removeConfirmBody}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <button
              type="button"
              onClick={() => setPendingDelete(null)}
              className="rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {copy.sidebar.removeCancel}
            </button>
            <button
              type="button"
              onClick={confirmDelete}
              className="rounded-md bg-destructive/15 px-3 py-1.5 text-sm text-destructive transition-colors hover:bg-destructive/25"
            >
              {copy.sidebar.removeConfirm}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SidebarGroup({
  label,
  entries,
  currentId,
  onDelete,
}: {
  label: string;
  entries: ResearchEntry[];
  currentId: string | null;
  onDelete: (entry: ResearchEntry) => void;
}) {
  return (
    <section className="mt-1 first:mt-0">
      <p className="px-2 pb-1 pt-2 text-[11px] font-medium tracking-wider text-muted-foreground/70 uppercase">
        {label}
      </p>
      <ul>
        {entries.map((entry) => (
          <SidebarItem
            key={entry.researchId}
            entry={entry}
            isCurrent={entry.researchId === currentId}
            onDelete={onDelete}
          />
        ))}
      </ul>
    </section>
  );
}

function SidebarItem({
  entry,
  isCurrent,
  onDelete,
}: {
  entry: ResearchEntry;
  isCurrent: boolean;
  onDelete: (entry: ResearchEntry) => void;
}) {
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(entry.title);
  const inputRef = useRef<HTMLInputElement>(null);

  const status = entry.status ? STATUS_ICON[entry.status] : NEW_ENTRY;
  const StatusIcon = status.icon;

  // 副标题：进行中状态给状态文案（等待输入的用琥珀色突出），
  // 其余给相对时间（now/Nm/Nh/Nd）；未提交的新会话给「待开始」
  const subtitle =
    entry.status === null
      ? copy.sidebar.statusNew
      : isActiveStatus(entry.status)
        ? copy.sidebar.statusText[
            entry.status as keyof typeof copy.sidebar.statusText
          ]
        : formatRelativeTime(entry.updatedAt);
  const subtitleClass =
    entry.status === "waiting_for_input"
      ? "text-chart-5"
      : "text-muted-foreground/70";

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  const startEditing = () => {
    setEditValue(entry.title);
    setEditing(true);
  };

  const confirmRename = () => {
    setEditing(false);
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== entry.title) {
      renameResearch(entry.researchId, trimmed);
    }
  };

  const open = () => router.push(`/research/${entry.researchId}`);

  return (
    <li
      className={cn(
        "group relative flex items-center rounded-md",
        isCurrent
          ? "bg-sidebar-accent text-sidebar-accent-foreground"
          : "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-sidebar-accent-foreground",
      )}
      onContextMenu={(e) => {
        if (editing) return;
        e.preventDefault();
        setMenuOpen(true);
      }}
    >
      {editing ? (
        <form
          className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            confirmRename();
          }}
        >
          <StatusIcon
            className={cn("size-4 shrink-0", status.className)}
            aria-hidden
          />
          <input
            ref={inputRef}
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={confirmRename}
            onKeyDown={(e) => {
              if (e.key === "Escape") setEditing(false);
            }}
            placeholder={copy.sidebar.renamePlaceholder}
            aria-label={copy.sidebar.rename}
            className="h-6 min-w-0 flex-1 rounded-sm border border-sidebar-ring bg-transparent px-1 text-sm outline-none"
          />
        </form>
      ) : (
        <>
          <Link
            href={`/research/${entry.researchId}`}
            className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5"
          >
            <StatusIcon
              className={cn(
                "size-4 shrink-0 self-start translate-y-[3px]",
                status.className,
                status.spin && "animate-spin",
              )}
              aria-hidden
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] leading-tight">
                {entry.title}
              </span>
              <span
                className={cn(
                  "mt-0.5 block truncate text-[11px] leading-tight",
                  subtitleClass,
                )}
              >
                {subtitle}
              </span>
            </span>
          </Link>
          <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
            <DropdownMenuTrigger
              aria-label={copy.sidebar.open}
              onClick={(e) => e.stopPropagation()}
              className="mr-1 flex size-6 shrink-0 items-center justify-center rounded-sm opacity-0 transition-opacity outline-none hover:bg-sidebar-accent focus-visible:opacity-100 group-hover:opacity-100 data-popup-open:opacity-100"
            >
              <Ellipsis className="size-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent side="bottom" align="end" className="w-auto">
              <DropdownMenuItem onClick={open}>
                <ExternalLink className="size-4" />
                {copy.sidebar.open}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={startEditing}>
                <Pencil className="size-4" />
                {copy.sidebar.rename}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="destructive"
                onClick={() => onDelete(entry)}
              >
                <Trash2 className="size-4" />
                {copy.sidebar.remove}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </>
      )}
    </li>
  );
}
