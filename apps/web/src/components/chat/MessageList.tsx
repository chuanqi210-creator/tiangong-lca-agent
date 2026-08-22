"use client";

import Link from "next/link";
import { FlaskConicalIcon } from "lucide-react";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { copy } from "@/lib/copy";
import { MessageItem } from "./MessageItem";
import type { Suggestion as SuggestionItem, UIMessage } from "./types";

/** 对话流：吸底滚动容器，渲染分发见 MessageItem */
export function MessageList({
  messages,
  suggestions,
  onPickSuggestion,
  onResponsibilityChoice,
  onGapAnswer,
}: {
  messages: UIMessage[];
  suggestions: SuggestionItem[];
  onPickSuggestion: (s: SuggestionItem) => void;
  onResponsibilityChoice: (messageId: string, choice: string) => void;
  onGapAnswer: (text: string) => void;
}) {
  return (
    <Conversation>
      <ConversationContent className="mx-auto min-h-full w-full max-w-3xl gap-8 px-4 py-6 sm:px-6">
        {messages.length === 0 ? (
          <ConversationEmptyState
            className="my-auto"
            title={copy.agent.name}
            description={copy.chat.emptyBody}
          >
            <div className="flex flex-col items-center gap-3">
              <FlaskConicalIcon
                className="size-8 text-muted-foreground"
                aria-hidden="true"
              />
              <div className="space-y-1">
                <h3 className="text-sm font-medium">{copy.agent.name}</h3>
                <p className="text-sm text-muted-foreground">
                  {copy.chat.emptyBody}
                </p>
              </div>
              <Link
                href="/projects"
                className="mt-2 rounded-full border border-border px-3 py-1.5 font-mono text-[11px] text-muted-foreground transition-colors hover:border-chart-1/50 hover:text-chart-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {copy.chat.emptyTaskLibrary} →
              </Link>
            </div>
          </ConversationEmptyState>
        ) : (
          messages.map((m) => (
            <MessageItem
              key={m.id}
              message={m}
              suggestions={suggestions}
              onPickSuggestion={onPickSuggestion}
              onResponsibilityChoice={onResponsibilityChoice}
              onGapAnswer={onGapAnswer}
            />
          ))
        )}
      </ConversationContent>
      <ConversationScrollButton />
    </Conversation>
  );
}
