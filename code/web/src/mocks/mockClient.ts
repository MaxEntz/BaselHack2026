import type { ChatClient } from "../chat/client.ts";
import type { ChatEvent, LiveEvent, SessionOutput } from "../types.ts";
import cards from "./cards.json";
import notification from "./notification.json";
import onboarding from "./onboarding.json";
import session from "./session.json";
import tonight from "./tonight.json";

// `npm run check:contracts` checks these JSON files against the types.
const ONBOARDING = onboarding as ChatEvent[];
const TONIGHT = tonight as ChatEvent[];
const CARDS = cards as ChatEvent[];
const NOTIFICATION = notification as LiveEvent;
const SESSION = session as SessionOutput;

const NOTIFICATION_DELAY_MS = 6000;

// Replays the contract mocks with realistic delays (VITE_MOCK=1).
export function createMockClient(onboarded: boolean): ChatClient {
  let website: string | null = null;
  let notified = false;

  function script(message: string): ChatEvent[] {
    if (onboarded) {
      return /block|busy|stat|polic|party|max/i.test(message) ? CARDS : TONIGHT;
    }
    if (website) {
      onboarded = true;
      return ONBOARDING;
    }
    const url = message.match(/\S+\.\S+/);
    if (!url) return reply("What's the address of your restaurant's website?");
    website = url[0];
    return reply(
      "Thanks! Now describe how you work in a few words: your tables, " +
        "your opening hours and the largest group you accept.",
    );
  }

  return {
    async *send(message, _sessionId, signal) {
      for (const event of script(message)) {
        if (event.type === "text") {
          // Stream word by word, like the real assistant.
          for (const word of event.delta.match(/\S+\s*/g) ?? []) {
            await sleep(35, signal);
            yield { type: "text", delta: word };
          }
          continue;
        }
        await sleep(delayBefore(event), signal);
        yield event;
      }
    },

    subscribe(_slug, { onEvent, onConnection }) {
      const timers = [window.setTimeout(() => onConnection(true), 300)];
      if (!notified) {
        timers.push(
          window.setTimeout(() => {
            notified = true;
            onEvent(NOTIFICATION);
          }, NOTIFICATION_DELAY_MS),
        );
      }
      return () => timers.forEach((timer) => window.clearTimeout(timer));
    },

    async session() {
      return onboarded ? SESSION : { merchant: null };
    },
  };
}

function reply(text: string): ChatEvent[] {
  return [{ type: "text", delta: text }, { type: "done" }];
}

function delayBefore(event: ChatEvent): number {
  switch (event.type) {
    case "step":
      return event.status === "running"
        ? 150
        : Math.min(Math.max(event.duration_ms ?? 0, 400), 1500);
    case "tool_start":
      return 300;
    case "card":
      return 500;
    default:
      return 100;
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(signal.reason);
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timer);
        reject(signal.reason);
      },
      { once: true },
    );
  });
}
