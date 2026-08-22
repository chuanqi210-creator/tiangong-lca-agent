"use client";

import { ChevronDownIcon, ListChecksIcon } from "lucide-react";
import { useState } from "react";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Task, TaskContent, TaskTrigger } from "@/components/ai-elements/task";
import { copy } from "@/lib/copy";

/**
 * 活动块：ai-elements Task 折叠容器承载工作叙述。
 * 进行中默认展开——✓ 已完成行 / › 当前行（shimmer 文字 + 闪烁光标）；
 * job 终态后自动折叠为一行摘要（"已完成 · N 步"），点击可再展开。
 */
export function ActivityBlock({
  items,
  active,
}: {
  items: { text: string; done: boolean }[];
  active: boolean;
}) {
  // 用户手动展开/折叠的覆盖值；未覆盖时跟随 active：
  // 进行中默认展开，终态（active=false）后自动折叠为一行摘要
  const [override, setOverride] = useState<boolean | null>(null);
  const open = override ?? active;

  const title = active
    ? copy.chat.activityTitle
    : `${copy.chat.activityDone} · ${items.length} ${copy.chat.activityStepUnit}`;

  return (
    <Task open={open} onOpenChange={setOverride}>
      <TaskTrigger
        title={title}
        className="gap-1.5 text-xs text-muted-foreground/70 hover:text-foreground"
      >
        <ChevronDownIcon className="size-3.5 -rotate-90 transition-transform group-data-[state=open]:rotate-0" />
        <ListChecksIcon className="size-3.5" />
        <p className="text-xs">{title}</p>
      </TaskTrigger>
      <TaskContent>
        <ul className="space-y-1.5">
          {items.map((item, i) => (
            <li
              key={`${i}-${item.text}`}
              className="font-mono text-xs leading-6"
            >
              {item.done ? (
                <>
                  <span className="mr-2 text-chart-1">✓</span>
                  <span className="text-muted-foreground">{item.text}</span>
                </>
              ) : (
                <>
                  <span className="mr-2 text-chart-1">›</span>
                  <Shimmer as="span">{item.text}</Shimmer>
                  <span className="ml-1 animate-pulse text-chart-1">▍</span>
                </>
              )}
            </li>
          ))}
        </ul>
      </TaskContent>
    </Task>
  );
}
