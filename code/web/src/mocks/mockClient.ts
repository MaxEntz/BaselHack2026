import { ChatError, type ChatClient } from "../chat/client.ts";
import type { ChatEvent, LiveEvent, SessionOutput } from "../types.ts";
import cards from "./cards.json";
import notification from "./notification.json";
import onboarding from "./onboarding.json";
import session from "./session.json";
import { MOCK_SITE_PAGE } from "./site.ts";
import tonight from "./tonight.json";

// `npm run check:contracts` checks these JSON files against the types.
// With the page the crawl read, as soon as `crawl` is done (#151).
const ONBOARDING = (onboarding as ChatEvent[]).flatMap((event): ChatEvent[] =>
  event.type === "step" && event.step === "crawl" && event.status === "done"
    ? [event, { type: "site_page", page: MOCK_SITE_PAGE }]
    : [event],
);
const TONIGHT = tonight as ChatEvent[];
const CARDS = cards as ChatEvent[];
const NOTIFICATION = notification as LiveEvent;
const SESSION = session as SessionOutput;

// "Publish it" after the onboarding: the `deploy` step the build left waiting (#136).
const PUBLISH: ChatEvent[] = [
  { type: "tool_start", tool: "publish_agent", label: "Publishing your agent…" },
  { type: "step", step: "deploy", status: "running" },
  { type: "step", step: "deploy", status: "done", detail: "Your agent is live.", duration_ms: 12 },
  { type: "card", kind: "confirmation", data: { text: "Your agent is live: Claude users can now book you." } },
  { type: "done" },
];

const NOTIFICATION_DELAY_MS = 6000;
// A description containing "cut" drops the stream while `extract` runs (#86).
const CUT = /\bcut\b/i;
const CUT_AT = ONBOARDING.findIndex(
  (event) => event.type === "step" && event.step === "extract" && event.status === "running",
);

// Replays the contract mocks with realistic delays (VITE_MOCK=1).
export function createMockClient(onboarded: boolean): ChatClient {
  let website: string | null = null;
  let notified = false;

  function script(message: string): ChatEvent[] {
    if (onboarded) {
      if (/publi/i.test(message)) return PUBLISH;
      return /block|busy|stat|polic|party|max/i.test(message) ? CARDS : TONIGHT;
    }
    if (website) {
      if (CUT.test(message)) return ONBOARDING.slice(0, CUT_AT + 1);
      onboarded = true;
      return ONBOARDING;
    }
    const url = message.match(/\S+\.\S+/);
    if (!url) return reply("What's the address of your restaurant's website?");
    website = url[0];
    // Studio sends the address and the description in one message.
    const words = message.replace(url[0], "").split(/\s+/).filter(Boolean);
    if (words.length >= 3) {
      if (CUT.test(message)) return ONBOARDING.slice(0, CUT_AT + 1);
      onboarded = true;
      return ONBOARDING;
    }
    return reply(
      "Thanks! Now describe how you work in a few words: your tables, " +
        "your opening hours and the largest group you accept.",
    );
  }

  return {
    async *send(message, _sessionId, signal) {
      const events = script(message);
      for (const event of events) {
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
      if (events.at(-1)?.type !== "done") {
        // What `httpClient` raises when the stream ends without `done`.
        await sleep(300, signal);
        throw new ChatError("The connection was interrupted before the reply ended.");
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

    async claim() {
      // No cookies without a platform.
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
