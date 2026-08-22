"use client";

import { motion } from "framer-motion";
import { copy } from "@/lib/copy";
import type { MaterialItem } from "@/components/chat/types";
import { MaterialPanel } from "@/components/materials/MaterialPanel";

export interface TodoState {
  mode: "need-user" | "agent-working";
  text: string;
}

const microLabel = "font-mono text-[11px] tracking-widest uppercase";

/**
 * 任务侧栏：研究目标、用户待办、当前推进状态、已收材料。
 * hairline 分节 + 大留白；"现在需要你"用青绿描边小框突出。
 * lg 以上固定在研究页左栏；窄屏由 ResearchShell 顶部的可展开条收纳同一份内容。
 */
export function TaskPanel({
  goal,
  materials,
  status,
  todo,
  researchId,
}: {
  goal: string;
  materials: MaterialItem[];
  status: string;
  todo: TodoState;
  researchId: string;
}) {
  return (
    <div className="flex h-full flex-col gap-7 overflow-y-auto p-5">
      <section>
        <h3 className={`${microLabel} text-muted-foreground`}>
          {copy.chat.goalLabel}
        </h3>
        <p className="mt-2 text-sm leading-6 text-foreground">{goal}</p>
      </section>

      <section className="border-t border-border pt-5">
        <h3 className={`${microLabel} text-muted-foreground`}>
          {copy.chat.todoLabel}
        </h3>
        {todo.mode === "need-user" ? (
          <motion.div
            key="need-user"
            initial={{ opacity: 0.2 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.3 }}
            className="mt-2 rounded-md border border-chart-1/60 px-3 py-2.5"
          >
            <p className={`${microLabel} text-chart-1`}>
              NOW / {copy.chat.todoNeedYou}
            </p>
            <p className="mt-1 text-sm leading-6 text-foreground">{todo.text}</p>
          </motion.div>
        ) : (
          <div className="mt-2 flex items-start gap-2">
            <motion.span
              className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-chart-1"
              animate={{ opacity: [0.25, 1, 0.25] }}
              transition={{ duration: 1.6, repeat: Infinity }}
            />
            <div>
              <p className={`${microLabel} text-muted-foreground`}>
                {copy.chat.todoAgentWorking}
              </p>
              <p className="mt-0.5 text-sm leading-6 text-muted-foreground">
                {todo.text}
              </p>
            </div>
          </div>
        )}
      </section>

      <section className="border-t border-border pt-5">
        <h3 className={`${microLabel} text-muted-foreground`}>
          {copy.chat.statusLabel}
        </h3>
        <motion.p
          key={status}
          initial={{ opacity: 0.2 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3 }}
          className="mt-2 font-mono text-xs leading-6 text-muted-foreground"
        >
          <span className="mr-2 text-chart-1">›</span>
          {status}
        </motion.p>
      </section>

      <section className="border-t border-border pt-5">
        <h3 className={`${microLabel} text-muted-foreground`}>
          {copy.chat.materialsLabel}
          <span className="ml-2 tabular-nums">{materials.length}</span>
        </h3>
        {materials.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground/70">
            {copy.chat.materialsEmpty}
          </p>
        ) : (
          <ul className="mt-2 border-t border-border">
            {materials.map((m) => (
              <motion.li
                key={m.id}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                className="border-b border-border py-2"
              >
                <p className="truncate text-sm text-foreground">{m.name}</p>
                <p className="font-mono text-[11px] text-muted-foreground/70">
                  {m.kind}
                  {m.note ? ` · ${m.note}` : ""}
                </p>
              </motion.li>
            ))}
          </ul>
        )}
      </section>

      <section className="border-t border-border pt-5">
        <MaterialPanel researchId={researchId} />
      </section>
    </div>
  );
}
