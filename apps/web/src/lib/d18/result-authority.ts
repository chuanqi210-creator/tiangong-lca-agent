import type { UIMessage } from "../../components/chat/types";

/** Replace an optimistic local result card with the accepted Project Record view. */
export function replaceWithAuthoritativeResult(
  messages: UIMessage[],
  resultId: string,
  authoritative: Extract<UIMessage, { kind: "card" }>,
  resolvedInputCardId?: string,
): UIMessage[] {
  const closedMessages = messages.map((message) =>
    resolvedInputCardId &&
    message.kind === "card" &&
    message.card.id === resolvedInputCardId
      ? { ...message, handled: true }
      : message,
  );
  const index = closedMessages.findIndex(
    (message) => message.kind === "card" && message.card.id === resultId,
  );
  if (index < 0) return [...closedMessages, authoritative];
  return closedMessages.map((message, position) =>
    position === index ? authoritative : message,
  );
}
