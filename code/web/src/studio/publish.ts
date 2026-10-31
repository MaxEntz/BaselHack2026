import { ChatError, type ChatClient } from "../chat/client.ts";
import { type ManagementClient, ManagementRequestError } from "../management/client.ts";
import type { ChatEvent } from "../types.ts";

export const PUBLISH = "Publish";

// The top bar's Publish: the route, with no model in the way. Without the owner cookie (the
// claim failed or came too late, ADR-0018) the route answers 401, while the chat session can
// still publish: the assistant takes over.
export async function* publishing(
  management: Pick<ManagementClient, "publish">,
  chat: Pick<ChatClient, "send">,
  slug: string,
  sessionId: string,
  signal: AbortSignal,
): AsyncGenerator<ChatEvent> {
  yield { type: "step", step: "deploy", status: "running" };
  try {
    await management.publish(slug);
  } catch (error) {
    if (error instanceof ManagementRequestError && error.status === 401) {
      yield* chat.send(PUBLISH, sessionId, signal);
      return;
    }
    throw new ChatError(error instanceof Error ? error.message : "I couldn't publish your agent.");
  }
  yield { type: "step", step: "deploy", status: "done" };
  yield { type: "done" };
}
