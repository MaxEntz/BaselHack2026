import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { ChatEvent } from "../types.ts";
import type { ChatClient } from "./client.ts";
import { ChatError } from "./client.ts";
import { chime } from "./chime.ts";
import type { AgentStatus, Merchant } from "./state.ts";
import { chatReducer, initialChatState } from "./state.ts";

// Kept across reloads (not across tabs) so the platform keeps the same session.
const SESSION_KEY = "merchant-agents:session";

export type Welcome = { onboarding: string; operations: string };

// `wire`: what the platform receives, when it isn't what the merchant typed.
export type SendOptions = { wire?: string; site?: string };

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
  // False from a build's card until this browser has the owner cookie (#132).
  const [claimed, setClaimed] = useState(true);
  const session = useRef<string>(null);
  session.current ??= storedSessionId();
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
      const { merchant } = await client.session(session.current as string, signal);
      if (merchant && !signal?.aborted) {
        dispatch({ type: "attached", merchant, welcome: welcome.operations });
      }
    },
    [client, welcome.operations],
  );

  useEffect(() => {
    const abort = new AbortController();
    void refresh(abort.signal);
    return () => abort.abort();
  }, [refresh]);

  const stream = useCallback(
    async (
      text: string,
      site: string | undefined,
      open: (signal: AbortSignal, sessionId: string) => AsyncIterable<ChatEvent>,
    ) => {
      if (busy.current) return;
      busy.current = true;
      dispatch({ type: "send", text, site });
      const abort = new AbortController();
      controller.current = abort;
      try {
        for await (const event of open(abort.signal, session.current as string)) {
          dispatch({ type: "chat", event });
          if (event.type === "card" && event.kind === "profile" && event.login_url) {
            setClaimed(false);
            void client.claim(session.current as string).finally(() => setClaimed(true));
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
    [client, refresh],
  );

  const send = useCallback(
    (text: string, { wire, site }: SendOptions = {}) =>
      stream(text, site, (signal) =>
        client.send(wire ?? text, session.current as string, signal),
      ),
    [client, stream],
  );

  const askDetails = useCallback(
    (text: string, address: string, site: string, reply: string) =>
      dispatch({ type: "details", text, address, site, reply }),
    [],
  );

  const setStatus = useCallback(
    (status: AgentStatus) => dispatch({ type: "status", status }),
    [],
  );

  // A new conversation, on a new session: the old one stays with its merchant.
  const reset = useCallback((text: string) => {
    controller.current?.abort();
    busy.current = false;
    session.current = replaceSessionId();
    setClaimed(true);
    dispatch({ type: "reset", welcome: text });
  }, []);

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

  return { state, send, stream, askDetails, setStatus, reset, claimed, liveConnected };
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

function replaceSessionId(): string {
  const id = newSessionId();
  try {
    window.sessionStorage.setItem(SESSION_KEY, id);
  } catch {
    // Storage blocked: a reload starts a new session.
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
