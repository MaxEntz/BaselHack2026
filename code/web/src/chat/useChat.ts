import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { announceChange } from "../management/changes.ts";
import { newKey } from "../management/keys.ts";
import type { ChatEvent, ConversationItem, LiveEvent } from "../types.ts";
import { changeWatcher } from "./changes.ts";
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

// The management site's panel (#140): the platform's session, the conversation it kept, and
// live events from the shell, which already listens to them.
export type ChatOptions = {
  sessionId?: string;
  restore?: readonly ConversationItem[];
  listen?: boolean;
};

export function useChat(
  client: ChatClient,
  welcome: Welcome,
  merchant: Merchant | null,
  { sessionId, restore, listen = true }: ChatOptions = {},
) {
  const [state, dispatch] = useReducer(chatReducer, null, () => {
    const initial = initialChatState(merchant ? welcome.operations : welcome.onboarding, merchant);
    return restore ? chatReducer(initial, { type: "restore", items: restore }) : initial;
  });
  const [liveConnected, setLiveConnected] = useState(false);
  // False from a build's card until this browser has the owner cookie (#132).
  const [claimed, setClaimed] = useState(true);
  const [firstSession] = useState(() => sessionId ?? storedSessionId());
  const session = useRef(firstSession);
  const busy = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const knownSlug = useRef(state.merchant?.slug ?? null);
  useEffect(() => {
    knownSlug.current = state.merchant?.slug ?? null;
  }, [state.merchant]);

  // Ask the platform which merchant the session works on (#88): after a
  // reload, or once a turn attached one without a profile card.
  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const { merchant } = await client.session(session.current, signal);
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
      const changed = changeWatcher();
      try {
        for await (const event of open(abort.signal, session.current)) {
          // A turn `reset` stopped says nothing more in the new conversation.
          if (abort.signal.aborted) break;
          dispatch({ type: "chat", event });
          if (changed(event) && knownSlug.current) announceChange(knownSlug.current);
          if (event.type === "card" && event.kind === "profile" && event.login_url) {
            setClaimed(false);
            void client.claim(session.current).finally(() => setClaimed(true));
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
        // After a `reset`, a newer turn may already be running: it is the one that frees the chat.
        if (controller.current === abort) busy.current = false;
      }
      if (!knownSlug.current && !abort.signal.aborted) void refresh();
    },
    [client, refresh],
  );

  const send = useCallback(
    (text: string, { wire, site }: SendOptions = {}) =>
      stream(text, site, (signal) =>
        client.send(wire ?? text, session.current, signal),
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

  // A live event the caller received itself.
  const notify = useCallback((event: LiveEvent) => dispatch({ type: "live", event }), []);

  const slug = state.merchant?.slug;
  useEffect(() => {
    if (!slug || !listen) return;
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
  }, [client, slug, listen]);

  return { state, send, stream, askDetails, setStatus, reset, notify, claimed, liveConnected };
}

function storedSessionId(): string {
  try {
    const stored = window.sessionStorage.getItem(SESSION_KEY);
    if (stored) return stored;
  } catch {
    // Storage blocked: a reload starts a new session.
  }
  return replaceSessionId();
}

function replaceSessionId(): string {
  // The id stands for the session's merchant (#132): a random UUID, never Math.random, which
  // can be guessed. `newKey` builds one where randomUUID is missing too (a projector on a LAN IP).
  const id = newKey();
  try {
    window.sessionStorage.setItem(SESSION_KEY, id);
  } catch {
    // Storage blocked: a reload starts a new session.
  }
  return id;
}
