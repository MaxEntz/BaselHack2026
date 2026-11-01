import type { ChatEvent, LiveEvent, SessionMerchant, SessionOutput } from "../types.ts";
import { type TypesOf, isRecord, typesOf } from "../json.ts";
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

// The events and cards this build can draw, every one of `ChatEvent` (../types.ts): `tsc` fails
// until a new one is listed. A newer platform's other ones are skipped, never handed to the
// reducer (#220). Only `type` and `kind` are checked: the rest of a frame is the contract's.
const CHAT_EVENTS = typesOf<ChatEvent>({
  text: true,
  tool_start: true,
  site_page: true,
  step: true,
  card: true,
  error: true,
  done: true,
});
type CardKind = Extract<ChatEvent, { type: "card" }>["kind"];
const CARD_KINDS: ReadonlySet<string> = new Set(
  Object.keys({
    profile: true,
    reservations: true,
    stats: true,
    policy: true,
    confirmation: true,
    diff: true,
    view: true,
    page: true,
  } satisfies Record<CardKind, true>),
);
const LIVE_EVENTS = typesOf<LiveEvent>({ notification: true });

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
          const event = parseEvent(frame.data, CHAT_EVENTS);
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
      const event = parseEvent(message.data, LIVE_EVENTS);
      if (event) onEvent(event);
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
        const body: unknown = await response.json();
        const merchant = isRecord(body) ? body.merchant : null;
        if (isMerchant(merchant)) return { merchant: { slug: merchant.slug, name: merchant.name } };
      }
    } catch {
      // Unreachable platform: the chat reports it on the next message.
    }
    return { merchant: null };
  },
};

function isMerchant(value: unknown): value is SessionMerchant {
  return isRecord(value) && typeof value.slug === "string" && typeof value.name === "string";
}

// A frame whose `type`, and a card's `kind`, this build knows. Their other fields are the
// platform's contract (../types.ts), which `npm run check:contracts` holds the mocks to.
function parseEvent<T extends { type: string }>(data: string, { types }: TypesOf<T>): T | null {
  try {
    const value: unknown = JSON.parse(data);
    if (!isRecord(value) || typeof value.type !== "string" || !types.has(value.type)) return null;
    if (value.type === "card" && !(typeof value.kind === "string" && CARD_KINDS.has(value.kind))) return null;
    return value as T;
  } catch {
    // Ignore malformed frames rather than breaking the whole turn.
  }
  return null;
}

// `?invite=<code>`: lets this browser start a build (POST /invite sets a cookie, #132).
// True when the platform took it, or asks for no invitation; false once `signal` aborts it.
export async function acceptInvite(code: string, signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch("/api/invite", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ code }),
      credentials: "same-origin",
      signal,
    });
    return response.ok;
  } catch {
    return false;
  }
}
