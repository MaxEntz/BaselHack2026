import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { ChatClient } from "./client.ts";
import { ChatError } from "./client.ts";
import { chime } from "./chime.ts";
import type { Merchant } from "./state.ts";
import { chatReducer, initialChatState } from "./state.ts";

// Kept across reloads (not across tabs) so the platform keeps the same session.
const SESSION_KEY = "merchant-agents:session";

export type Welcome = { onboarding: string; operations: string };

export function useChat(
  client: ChatClient,
  welcome: Welcome,
  merchant: Merchant | null,
) {
  const [state, dispatch] = useReducer(chatReducer, null, () =>
    initialChatState(
      merchant ? welcome.operations : welcome.onboarding,
      merchant,
    ),
  );
  const [liveConnected, setLiveConnected] = useState(false);
  const [sessionId] = useState(storedSessionId);
  const busy = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const known = useRef(state.merchant !== null);
  useEffect(() => {
    known.current = state.merchant !== null;
  }, [state.merchant]);

  // Ask the platform which merchant the session works on (#88): after a
  // reload, or once a turn attached one without a profile card.
  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const { merchant } = await client.session(sessionId, signal);
      if (merchant && !signal?.aborted) {
        dispatch({ type: "attached", merchant, welcome: welcome.operations });
      }
    },
    [client, sessionId, welcome.operations],
  );

  useEffect(() => {
    const abort = new AbortController();
    void refresh(abort.signal);
    return () => abort.abort();
  }, [refresh]);

  const send = useCallback(
    async (text: string) => {
      if (busy.current) return;
      busy.current = true;
      dispatch({ type: "send", text });
      const abort = new AbortController();
      controller.current = abort;
      try {
        for await (const event of client.send(text, sessionId, abort.signal)) {
          dispatch({ type: "chat", event });
          if (event.type === "card" && event.kind === "profile" && event.login_url) {
            void client.claim(sessionId);
          }
        }
      } catch (error) {
        if (!abort.signal.aborted) {
          dispatch({
            type: "failed",
            message:
              error instanceof ChatError
                ? error.message
                : "Something went wrong while answering.",
          });
        }
      } finally {
        busy.current = false;
      }
      if (!known.current && !abort.signal.aborted) void refresh();
    },
    [client, sessionId, refresh],
  );

  useEffect(() => () => controller.current?.abort(), []);

  const slug = state.merchant?.slug;
  useEffect(() => {
    if (!slug) return;
    const unsubscribe = client.subscribe(slug, {
      onEvent: (event) => {
        dispatch({ type: "live", event });
        chime();
      },
      onConnection: setLiveConnected,
    });
    return () => {
      unsubscribe();
      setLiveConnected(false);
    };
  }, [client, slug]);

  return { state, send, liveConnected };
}

function storedSessionId(): string {
  try {
    const stored = window.sessionStorage.getItem(SESSION_KEY);
    if (stored) return stored;
  } catch {
    // Storage blocked: a reload starts a new session.
  }
  const id = newSessionId();
  try {
    window.sessionStorage.setItem(SESSION_KEY, id);
  } catch {
    // Same.
  }
  return id;
}

function newSessionId(): string {
  // randomUUID needs a secure context; a projector on a LAN IP may not be one. The id
  // stands for the session's merchant (#132): never Math.random, which can be guessed.
  if (window.isSecureContext) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return `s-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}
