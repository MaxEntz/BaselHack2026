import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { ChatClient } from "./client.ts";
import { ChatError } from "./client.ts";
import { chime } from "./chime.ts";
import type { Merchant } from "./state.ts";
import { chatReducer, initialChatState } from "./state.ts";

export function useChat(
  client: ChatClient,
  welcome: string,
  merchant: Merchant | null,
) {
  const [state, dispatch] = useReducer(chatReducer, null, () =>
    initialChatState(welcome, merchant),
  );
  const [liveConnected, setLiveConnected] = useState(false);
  const [sessionId] = useState(newSessionId);
  const busy = useRef(false);
  const controller = useRef<AbortController | null>(null);

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
    },
    [client, sessionId],
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

function newSessionId(): string {
  // randomUUID needs a secure context; a projector on a LAN IP may not be one.
  if (window.isSecureContext) return crypto.randomUUID();
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
