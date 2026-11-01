import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { ActionDispatch } from "react";
import { announceChange } from "../management/changes.ts";
import { newKey } from "../management/keys.ts";
import type { ChatEvent, ConversationItem, LiveEvent } from "../types.ts";
import { changeWatcher } from "./changes.ts";
import type { ChatClient } from "./client.ts";
import { ChatError } from "./client.ts";
import { chime } from "./chime.ts";
import type { AgentStatus, ChatAction, Merchant } from "./state.ts";
import { chatReducer, initialChatState } from "./state.ts";

// Kept across reloads (not across tabs) so the platform keeps the same session.
const SESSION_KEY = "merchant-agents:session";
const FAILED = "Something went wrong while answering.";

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

type Dispatch = ActionDispatch<[action: ChatAction]>;
type OpenTurn = (signal: AbortSignal, sessionId: string) => AsyncIterable<ChatEvent>;

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

  // Ask the platform which merchant the session works on (#88): after a reload, or once a turn
  // attached one without a profile card. An answer about a session a reset left is dropped.
  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const asked = session.current;
      const { merchant } = await client.session(asked, signal);
      if (merchant && !signal?.aborted && session.current === asked) {
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
    async (text: string, site: string | undefined, open: OpenTurn) => {
      if (busy.current) return;
      busy.current = true;
      dispatch({ type: "send", text, site });
      const abort = new AbortController();
      controller.current = abort;
      const changed = changeWatcher();
      await playTurn(() => open(abort.signal, session.current), abort.signal, dispatch, (event) => {
        if (changed(event) && knownSlug.current) announceChange(knownSlug.current);
        if (event.type === "card" && event.kind === "profile" && event.login_url) {
          setClaimed(false);
          void client.claim(session.current).finally(() => setClaimed(true));
        }
      });
      // After a `reset`, a newer turn may already be running: it is the one that frees the chat.
      if (controller.current === abort) busy.current = false;
      if (!knownSlug.current && !abort.signal.aborted) void refresh();
    },
    [client, refresh],
  );

  const send = useCallback(
    (text: string, { wire, site }: SendOptions = {}) =>
      stream(text, site, (signal) => client.send(wire ?? text, session.current, signal)),
    [client, stream],
  );

  const askDetails = useCallback(
    (text: string, address: string, site: string, reply: string) =>
      dispatch({ type: "details", text, address, site, reply }),
    [],
  );

  const setStatus = useCallback((status: AgentStatus) => dispatch({ type: "status", status }), []);

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

  const liveConnected = useLiveEvents(client, listen ? state.merchant?.slug : undefined, dispatch);

  return { state, send, stream, askDetails, setStatus, reset, notify, claimed, liveConnected };
}

// One turn's events into the conversation, until it ends or `signal` stops it. Never throws:
// a failure becomes the chat's error bubble.
async function playTurn(
  open: () => AsyncIterable<ChatEvent>,
  signal: AbortSignal,
  dispatch: Dispatch,
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  try {
    for await (const event of open()) {
      // A turn `reset` stopped says nothing more in the new conversation.
      if (signal.aborted) break;
      dispatch({ type: "chat", event });
      onEvent(event);
    }
  } catch (error) {
    if (!signal.aborted) {
      dispatch({ type: "failed", message: error instanceof ChatError ? error.message : FAILED });
    }
  }
}

// The merchant's live events, with a chime; no merchant, no subscription. True while connected.
function useLiveEvents(client: ChatClient, slug: string | undefined, dispatch: Dispatch): boolean {
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    if (!slug) return;
    const unsubscribe = client.subscribe(slug, {
      onEvent: (event) => {
        dispatch({ type: "live", event });
        chime();
      },
      onConnection: setConnected,
    });
    return () => {
      unsubscribe();
      setConnected(false);
    };
  }, [client, slug, dispatch]);
  return connected;
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
