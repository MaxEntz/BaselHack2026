import type { ChatEvent, LiveEvent, SessionOutput } from "../types.ts";
import { readSse } from "./sse.ts";

export type LiveHandlers = {
  onEvent: (event: LiveEvent) => void;
  onConnection: (connected: boolean) => void;
};

export type ChatClient = {
  // Streams one assistant turn; throws if the connection fails before `done`.
  send: (
    message: string,
    sessionId: string,
    signal: AbortSignal,
  ) => AsyncGenerator<ChatEvent>;
  // Subscribes to live notifications; returns the unsubscribe function.
  subscribe: (slug: string, handlers: LiveHandlers) => () => void;
  // The merchant the session works on; no merchant when the platform can't say.
  session: (sessionId: string, signal?: AbortSignal) => Promise<SessionOutput>;
  // After a build: this browser gets the owner cookie, so its live events open (#132).
  claim: (sessionId: string) => Promise<void>;
};

export class ChatError extends Error {}

export const NEEDS_INVITE =
  "Building a new agent needs an invitation: open the link you were given.";

// A silent network drop never ends the stream: give up after this long without
// any byte. The platform's heartbeat (a `: ping` comment every 15 s) keeps a slow
// turn alive (#87).
const IDLE_TIMEOUT_MS = 90_000;
const LIVE_RETRY_MS = 2000;

export const httpClient: ChatClient = {
  async *send(message, sessionId, signal) {
    const idle = new AbortController();
    const streamSignal = AbortSignal.any([signal, idle.signal]);
    let timer = 0;
    const touch = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => idle.abort(), IDLE_TIMEOUT_MS);
    };
    touch();

    try {
      let response: Response;
      try {
        response = await fetch("/api/chat", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            accept: "text/event-stream",
          },
          body: JSON.stringify({ session_id: sessionId, message }),
          signal: streamSignal,
        });
      } catch (error) {
        if (signal.aborted) throw error;
        throw new ChatError("I can't reach the platform right now.");
      }
      if (response.status === 409) {
        throw new ChatError("I'm still answering your previous message.");
      }
      if (response.status === 403) {
        // PLATFORM_INVITE_CODE is set: a new build needs the invitation link (#132).
        throw new ChatError(NEEDS_INVITE);
      }
      if (!response.ok || !response.body) {
        throw new ChatError("The platform couldn't answer this message.");
      }

      try {
        for await (const frame of readSse(response.body, touch)) {
          const event = parseEvent<ChatEvent>(frame.data);
          if (!event) continue;
          yield event;
          if (event.type === "done") return;
        }
      } catch (error) {
        if (signal.aborted) throw error;
      }
      throw new ChatError(
        "The connection was interrupted before the reply ended.",
      );
    } finally {
      window.clearTimeout(timer);
    }
  },

  subscribe(slug, { onEvent, onConnection }) {
    const handle = (message: MessageEvent<string>) => {
      const event = parseEvent<LiveEvent>(message.data);
      if (event?.type === "notification") onEvent(event);
    };
    let source: EventSource;
    let retry = 0;
    const connect = () => {
      source = new EventSource(
        `/api/events?merchant=${encodeURIComponent(slug)}`,
      );
      source.addEventListener("notification", handle);
      source.addEventListener("message", handle);
      source.addEventListener("open", () => onConnection(true));
      source.addEventListener("error", () => {
        onConnection(false);
        // EventSource retries network errors on its own, but a non-200 answer
        // (API restarting, proxy 502) closes it for good: open a new one.
        if (source.readyState === EventSource.CLOSED) {
          retry = window.setTimeout(connect, LIVE_RETRY_MS);
        }
      });
    };
    connect();
    return () => {
      window.clearTimeout(retry);
      source.close();
    };
  },

  async claim(sessionId) {
    try {
      await fetch("/api/session/claim", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
        credentials: "same-origin",
      });
    } catch {
      // The private link on the card still opens the site.
    }
  },

  async session(sessionId, signal) {
    try {
      const response = await fetch(
        `/api/session?session_id=${encodeURIComponent(sessionId)}`,
        { signal },
      );
      if (response.ok) {
        const body = (await response.json()) as Partial<SessionOutput>;
        if (body.merchant) return { merchant: body.merchant };
      }
    } catch {
      // Unreachable platform: the chat reports it on the next message.
    }
    return { merchant: null };
  },
};

function parseEvent<T extends { type: string }>(data: string): T | null {
  try {
    const value: unknown = JSON.parse(data);
    if (typeof value === "object" && value !== null && "type" in value) {
      return value as T;
    }
  } catch {
    // Ignore malformed frames rather than breaking the whole turn.
  }
  return null;
}

// `?invite=<code>`: lets this browser start a build (POST /invite sets a cookie, #132).
// True when the platform took it, or asks for no invitation.
export async function acceptInvite(code: string): Promise<boolean> {
  try {
    const response = await fetch("/api/invite", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ code }),
      credentials: "same-origin",
    });
    return response.ok;
  } catch {
    return false;
  }
}
