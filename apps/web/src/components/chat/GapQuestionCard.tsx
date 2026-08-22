"use client";

import { MessageCircleQuestionIcon, SendIcon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { copy } from "@/lib/copy";
import type { GapQuestionCardData, Suggestion } from "@/components/chat/types";

/**
 * 缺口追问卡（参考 palot chat-question）：
 * 问题正文 + 快捷选项按钮 + 卡片内直接自由文本回答（与选项同一回答通道）。
 * handled 态显示已答内容并禁用交互。
 */
export function GapQuestionCard({
  card,
  suggestions,
  handled,
  answer,
  onPick,
  onAnswer,
}: {
  card: GapQuestionCardData;
  suggestions: Suggestion[];
  handled: boolean;
  answer?: string;
  onPick: (s: Suggestion) => void;
  onAnswer: (text: string) => void;
}) {
  const [text, setText] = useState("");

  const submit = () => {
    const t = text.trim();
    if (!t || handled) return;
    onAnswer(t);
    setText("");
  };

  return (
    <section
      aria-label={card.title}
      className="rounded-xl border border-border bg-card/50"
    >
      <div className="flex items-center gap-2 px-3 py-2.5">
        <MessageCircleQuestionIcon
          className="size-4 shrink-0 text-chart-3"
          aria-hidden="true"
        />
        <span className="text-sm font-medium text-foreground">
          {card.title}
        </span>
      </div>

      <div className="space-y-3 border-t border-border px-3 py-3 text-sm leading-6">
        <div>
          <p className="text-xs text-muted-foreground">
            {copy.research.gapWhyLabel}
          </p>
          <p className="mt-0.5 text-foreground">{card.why}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">
            {copy.research.gapAcceptLabel}
          </p>
          <p className="mt-0.5 text-muted-foreground">{card.accept}</p>
        </div>
      </div>

      {handled ? (
        <div className="border-t border-border px-3 py-2">
          <p className="text-xs text-muted-foreground">
            {copy.research.gapAnswered}
            {answer ? `：${answer}` : ""}
          </p>
        </div>
      ) : (
        <div className="space-y-2 border-t border-border px-3 py-2.5">
          {suggestions.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {suggestions.map((s, i) => (
                <Button
                  key={s.id}
                  aria-label={
                    s.intent === "decline"
                      ? "d18-decision-unavailable"
                      : undefined
                  }
                  variant="outline"
                  size="sm"
                  onClick={() => onPick(s)}
                >
                  [{i + 1}] {s.label}
                </Button>
              ))}
            </div>
          )}
          <div className="flex items-end gap-2">
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder={copy.research.gapAnswerPlaceholder}
              rows={1}
              className="min-h-9 min-w-0 flex-1"
            />
            <Button size="sm" onClick={submit} disabled={!text.trim()}>
              <SendIcon aria-hidden="true" />
              {copy.research.gapAnswerSubmit}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
