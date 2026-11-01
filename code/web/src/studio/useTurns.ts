import { useRef, type RefObject } from "react";
import type { Merchant } from "../chat/state.ts";
import type { useChat } from "../chat/useChat.ts";
import { managementClient } from "../management/index.ts";
import { crossfade } from "../management/motion.ts";
import { CHAT_CLIENT, WELCOME } from "./chatClient.ts";
import type { ComposerHandle } from "./Composer.tsx";
import { EXAMPLES } from "./examples.ts";
import { type Phase, askForDetails, buildMessage, describesItself, parseAddress } from "./phase.ts";
import { PUBLISH, publishing } from "./publish.ts";
import type { CanvasState } from "./useCanvasView.ts";

type LastSend = { text: string; wire?: string; site?: string };

type TurnsInput = {
  chat: ReturnType<typeof useChat>;
  phase: Phase;
  canvas: CanvasState;
  // Forgets what the platform said of the merchant.
  forget: () => void;
  // The message box, which holds its own text.
  composer: RefObject<ComposerHandle | null>;
};

export type Turns = {
  say: (raw: string) => void;
  // A chip: an example's address opens it, any other chip is said.
  chip: (text: string) => void;
  retry: () => void;
  publish: () => void;
  openExample: (address: string, brief: string) => void;
  startOver: () => void;
  // A dot asks for a colour in words: the assistant checks its contrast.
  askForColour: () => void;
};

/** What the merchant's messages, chips and buttons start in the conversation. */
export function useTurns({ chat, phase, canvas, forget, composer }: TurnsInput): Turns {
  const { state, send, stream, reset } = chat;
  const last = useRef<LastSend | null>(null);
  const setText = (text: string) => composer.current?.setText(text);

  function publish() {
    const merchant = state.merchant?.slug;
    if (!merchant) return;
    canvas.quiet();
    last.current = { text: PUBLISH };
    crossfade(
      () =>
        void stream(PUBLISH, undefined, (signal, sessionId) =>
          publishing(managementClient, CHAT_CLIENT, merchant, sessionId, signal),
        ),
    );
  }

  function say(raw: string) {
    const text = raw.trim();
    if (!text || state.busy) return;
    canvas.quiet();
    if (phase === "address") {
      const found = parseAddress(text);
      // The text leaves the box inside the transition, so that it travels to its bubble.
      crossfade(() => {
        setText("");
        if (found && !describesItself(found)) {
          chat.askDetails(text, found.address, found.host, askForDetails(found.host));
          return;
        }
        last.current = { text, site: found?.host };
        void send(text, { site: found?.host });
      });
      return;
    }
    setText("");
    if (phase === "describe" && state.address) {
      const wire = buildMessage(state.address, text);
      last.current = { text, wire };
      void send(text, { wire });
    } else if (phase === "review" && text === PUBLISH) {
      publish();
    } else {
      last.current = { text };
      void send(text);
    }
  }

  function openExample(address: string, brief: string) {
    if (state.busy || !mayLeaveSite(state.merchant)) return;
    const text = `${address}, ${brief}`;
    const site = parseAddress(address)?.host;
    last.current = { text, site };
    crossfade(() => {
      if (state.turn > 0 || state.merchant) reset(WELCOME.onboarding);
      void send(text, { site });
    });
  }

  return {
    say,
    publish,
    openExample,
    chip(text) {
      const example = EXAMPLES.find((item) => item.address === text);
      if (example) openExample(example.address, example.brief);
      else say(text);
    },
    retry() {
      const previous = last.current;
      if (!previous) return;
      if (previous.text === PUBLISH && !previous.wire) publish();
      else void send(previous.text, previous);
    },
    startOver() {
      if (state.busy || !mayLeaveSite(state.merchant)) return;
      crossfade(() => {
        reset(WELCOME.onboarding);
        setText("");
        forget();
        canvas.clear();
      });
      composer.current?.focus({ preventScroll: true });
    },
    askForColour() {
      setText("Change the main colour to ");
      canvas.setPane("chat");
      composer.current?.focus();
    },
  };
}

// The owner cookie names one merchant (ADR-0018): a second build replaces it.
function mayLeaveSite(merchant: Merchant | null): boolean {
  if (!merchant) return true;
  return window.confirm(
    `Start a new site? This browser will then open only the new one. ${merchant.name} stays reachable through its private link.`,
  );
}
