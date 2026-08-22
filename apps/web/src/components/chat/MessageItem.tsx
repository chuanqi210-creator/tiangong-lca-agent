"use client";

import { useEffect, useRef, useState } from "react";
import { CheckIcon, CopyIcon } from "lucide-react";
import {
  Message,
  MessageAction,
  MessageActions,
  MessageContent,
  MessageResponse,
} from "@/components/ai-elements/message";
import { copy } from "@/lib/copy";
import { ActivityBlock } from "./ActivityBlock";
import { AttachmentCard } from "./AttachmentCard";
import { GapQuestionCard } from "./GapQuestionCard";
import { NoticeCard } from "./NoticeCard";
import { ResponsibilityCard } from "./ResponsibilityCard";
import { ResultCard } from "./ResultCard";
import type { Suggestion, UIMessage } from "./types";

/** 用户消息：右侧气泡 */
function UserMessageItem({ text }: { text: string }) {
  return (
    <Message from="user">
      <MessageContent>{text}</MessageContent>
    </Message>
  );
}

/** Agent 消息：左侧裸排版，正文走 streamdown（代码块自动 Shiki + 复制按钮） */
function AssistantMessageItem({
  text,
  streaming,
}: {
  text: string;
  streaming: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(false), 1500);
  };

  return (
    <Message from="assistant">
      <MessageContent>
        <MessageResponse>{text}</MessageResponse>
        {streaming && (
          <span className="ml-0.5 inline-block animate-pulse text-chart-1">
            ▍
          </span>
        )}
      </MessageContent>
      <MessageActions className="opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
        <MessageAction
          tooltip={copied ? copy.chat.copied : copy.chat.copyMessage}
          onClick={handleCopy}
        >
          {copied ? (
            <CheckIcon className="size-3.5" />
          ) : (
            <CopyIcon className="size-3.5" />
          )}
        </MessageAction>
      </MessageActions>
    </Message>
  );
}

/**
 * 渲染分发：每种 UIMessage 类型对应一个子组件。
 * MessageList 只做 map，类型判断全部收敛在这里。
 */
export function MessageItem({
  message,
  suggestions,
  onPickSuggestion,
  onResponsibilityChoice,
  onGapAnswer,
}: {
  message: UIMessage;
  suggestions: Suggestion[];
  onPickSuggestion: (s: Suggestion) => void;
  onResponsibilityChoice: (messageId: string, choice: string) => void;
  onGapAnswer: (text: string) => void;
}) {
  switch (message.kind) {
    case "user":
      return <UserMessageItem text={message.text} />;
    case "assistant":
      return (
        <AssistantMessageItem
          text={message.text}
          streaming={message.streaming}
        />
      );
    case "narrative":
      return <ActivityBlock items={message.items} active={message.active} />;
    case "attachments":
      return <AttachmentCard items={message.items} />;
    case "card":
      switch (message.card.kind) {
        case "gap_question":
          return (
            <GapQuestionCard
              card={message.card}
              suggestions={message.handled ? [] : suggestions}
              handled={message.handled}
              answer={message.choice}
              onPick={onPickSuggestion}
              onAnswer={onGapAnswer}
            />
          );
        case "responsibility":
          return (
            <ResponsibilityCard
              card={message.card}
              handled={message.handled}
              choice={message.choice}
              onChoice={(c) => onResponsibilityChoice(message.id, c)}
            />
          );
        case "result":
          return <ResultCard card={message.card} />;
        case "notice":
          return <NoticeCard card={message.card} />;
      }
  }
}
