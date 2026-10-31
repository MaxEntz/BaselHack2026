import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { ChatError, httpClient } from "../chat/client.ts";
import type { Merchant } from "../chat/state.ts";
import { useChat, type Welcome } from "../chat/useChat.ts";
import { managementClient } from "../management/index.ts";
import { createMockClient } from "../mocks/mockClient.ts";
import type { ChatEvent, MerchantOverview } from "../types.ts";
import { Canvas } from "./Canvas.tsx";
import { DEMO, EXAMPLES } from "./examples.ts";
import { type Addresses, Feed, type View } from "./feed.tsx";
import { crossfade, useSplitOpening } from "./motion.ts";
import {
  FIRST_PLACEHOLDER,
  PLACEHOLDERS,
  PUBLISH,
  askForDetails,
  buildMessage,
  chipsOf,
  describesItself,
  feedOf,
  initials,
  isBuilt,
  isEmpty,
  parseAddress,
  phaseOf,
  pillOf,
  profileOf,
  siteName,
} from "./phase.ts";
import "./studio.css";

const env = import.meta.env;
const DEMO_MERCHANT: Merchant | null = env.VITE_MERCHANT_SLUG
  ? { slug: env.VITE_MERCHANT_SLUG, name: env.VITE_MERCHANT_NAME || env.VITE_MERCHANT_SLUG }
  : null;
const CLIENT = env.VITE_MOCK === "1" ? createMockClient(DEMO_MERCHANT !== null) : httpClient;

const WELCOME: Welcome = {
  onboarding: "",
  operations:
    "Welcome back! Ask me about tonight's bookings, block a time slot or change your booking policy.",
};

const COPIED_MS = 2200;
const MAX_INPUT = 150;

type LastSend = { text: string; wire?: string; site?: string };

// From the platform once the owner cookie is set; until then, what the build said.
function addressesOf(
  slug: string | null,
  overview: MerchantOverview | null,
  mcpUrl: string | undefined,
): Addresses | null {
  if (slug && overview?.slug === slug) {
    return { agent: overview.agent_address, mcp: overview.mcp_url, management: overview.management_url };
  }
  if (!slug || !mcpUrl) return null;
  const { origin, hostname } = window.location;
  return {
    agent: `batp://${hostname}/${slug}`,
    mcp: `${new URL(mcpUrl).origin}/m/${slug}/mcp`,
    management: `${origin}/manage/${slug}`,
  };
}

export function StudioPage() {
  const chat = useChat(CLIENT, WELCOME, DEMO_MERCHANT);
  const { state, send, stream, reset, setStatus, claimed } = chat;
  const phase = phaseOf(state);
  const empty = isEmpty(state);
  const slug = state.merchant?.slug ?? null;
  const split = slug !== null;
  const entries = feedOf(state);
  const profile = profileOf(state.items);
  const chips = chipsOf(state, phase, EXAMPLES.map((example) => example.address));

  const [input, setInput] = useState("");
  const [view, setView] = useState<View>("manage");
  const [pane, setPane] = useState<"chat" | "canvas">("chat");
  const [overview, setOverview] = useState<MerchantOverview | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const last = useRef<LastSend | null>(null);
  useSplitOpening(split, body, canvas);

  useEffect(() => {
    document.title = "Sésame Studio";
  }, []);

  useLayoutEffect(() => {
    const element = textarea.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, MAX_INPUT)}px`;
  }, [input, empty]);

  // The addresses, and a merchant found again after a reload: is it live or still hidden?
  const restored = slug !== null && profile === null && state.turn === 0;
  const status = state.status;
  useEffect(() => {
    if (!slug || !claimed) return;
    const abort = new AbortController();
    managementClient
      .merchant(slug, abort.signal)
      .then((merchant) => {
        if (abort.signal.aborted) return;
        setOverview(merchant);
        if (restored) setStatus(merchant.status);
      })
      .catch(() => {
        // Not signed in, or the platform is away: the card falls back to what the build said.
      });
    return () => abort.abort();
  }, [slug, claimed, restored, status, setStatus]);

  const addresses = addressesOf(slug, overview, profile?.mcp_url);

  function publish() {
    if (!slug) return;
    const merchant = slug;
    async function* publishing(): AsyncGenerator<ChatEvent> {
      yield { type: "step", step: "deploy", status: "running" };
      try {
        await managementClient.publish(merchant);
      } catch (error) {
        throw new ChatError(error instanceof Error ? error.message : "I couldn't publish your agent.");
      }
      yield { type: "step", step: "deploy", status: "done" };
      yield { type: "done" };
    }
    last.current = { text: PUBLISH };
    crossfade(() => void stream(PUBLISH, undefined, publishing));
  }

  function say(raw: string) {
    const text = raw.trim();
    if (!text || state.busy) return;
    if (phase === "address") {
      const found = parseAddress(text);
      // The text leaves the box inside the transition, so that it travels to its bubble.
      crossfade(() => {
        setInput("");
        if (found && !describesItself(found)) {
          chat.askDetails(text, found.address, found.host, askForDetails(found.host));
          return;
        }
        last.current = { text, site: found?.host };
        void send(text, { site: found?.host });
      });
      return;
    }
    setInput("");
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

  function retry() {
    const previous = last.current;
    if (!previous) return;
    if (previous.text === PUBLISH && !previous.wire) publish();
    else void send(previous.text, previous);
  }

  function openExample(address: string, brief: string) {
    if (state.busy) return;
    const text = `${address}, ${brief}`;
    const site = parseAddress(address)?.host;
    last.current = { text, site };
    crossfade(() => {
      if (state.turn > 0 || state.merchant) reset(WELCOME.onboarding);
      void send(text, { site });
    });
  }

  function startOver() {
    if (state.busy) return;
    crossfade(() => {
      reset(WELCOME.onboarding);
      setInput("");
      setOverview(null);
      setPane("chat");
      setView("manage");
    });
    textarea.current?.focus({ preventScroll: true });
  }

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [toast]);

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setToast("Copied");
    } catch {
      setToast(`Copy this: ${text}`);
    }
  }

  function open(next: View) {
    crossfade(() => {
      setView(next);
      setPane("canvas");
    });
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    say(input);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      say(input);
    }
  };

  const name = siteName(state);
  const pill = pillOf(state);
  const pillReady = !restored || overview !== null;
  const built = isBuilt(state);
  const published = status === "live" || status === "down";
  const live = phase === "live";

  return (
    <section className="studio" data-split={split ? "1" : "0"} data-pane={pane} aria-label="Sésame">
      <header className="st-top">
        <img className="st-logo" src="/wordmark.png" alt="Sésame" />
        {name && <span className="st-sep" />}
        {name && <span className="st-name">{name}</span>}
        {pill && pillReady && (
          <span className={pill.live ? "st-status live" : "st-status"}>
            <i />
            <span>{pill.label}</span>
          </span>
        )}
        <div className="st-actions">
          <button type="button" className="st-btn out sm st-new" onClick={startOver}>
            New
          </button>
          {built && (
            <button
              type="button"
              className="st-btn pri sm"
              disabled={phase !== "review" || state.busy}
              onClick={publish}
            >
              {published ? "Published" : PUBLISH}
            </button>
          )}
        </div>
      </header>

      <div ref={body} className="st-body">
        <nav className="st-rail" aria-label="Restaurants">
          <button type="button" onClick={startOver}>
            <span className="st-tile plus">+</span>New
          </button>
          {state.merchant && (
            <button type="button" aria-current="true">
              <span className="st-tile">{initials(state.merchant.name)}</span>
              {state.merchant.name}
            </button>
          )}
          {DEMO && <span className="st-rail-label">Examples</span>}
          {EXAMPLES.map((example) => (
            <button
              key={example.slug}
              type="button"
              onClick={() => openExample(example.address, example.brief)}
            >
              <span className="st-tile" style={{ background: example.colour, color: example.ink }}>
                {initials(example.name)}
              </span>
              {example.name}
            </button>
          ))}
        </nav>

        {split && (
          <div className="st-pane-switch" role="group" aria-label="Pane">
            <button type="button" aria-pressed={pane === "chat"} onClick={() => setPane("chat")}>
              Conversation
            </button>
            <button type="button" aria-pressed={pane === "canvas"} onClick={() => setPane("canvas")}>
              Your sites
            </button>
          </div>
        )}

        <div className="st-chat" data-empty={empty ? "1" : undefined}>
          {empty ? (
            <div className="st-feed" role="log" aria-label="Conversation">
              <div className="st-hello">
                <h1>What's your website's address?</h1>
                <p>
                  I'll read it and ask what it doesn't say. Then I'll build your agent site, which has
                  no interface and answers agents directly, and your management site, in your own
                  colours. Your public site doesn't change.
                </p>
              </div>
            </div>
          ) : (
            <Feed
              entries={entries}
              activity={state.activity}
              busy={state.busy}
              addresses={addresses}
              onOpen={open}
              onCopy={(text) => void copy(text)}
              onRetry={retry}
            />
          )}
          <form className="st-composer" onSubmit={onSubmit}>
            {chips.length > 0 && (
              <div className={chips.length <= 3 ? "st-chips wrap" : "st-chips"}>
                {chips.map((chip) => (
                  <button
                    key={chip}
                    type="button"
                    className="st-chip"
                    onClick={() => {
                      const example = EXAMPLES.find((item) => item.address === chip);
                      if (example) openExample(example.address, example.brief);
                      else say(chip);
                    }}
                  >
                    {chip}
                  </button>
                ))}
              </div>
            )}
            <div className="st-box">
              <label className="st-sr" htmlFor="st-input">
                Message
              </label>
              <textarea
                id="st-input"
                ref={textarea}
                rows={1}
                value={input}
                placeholder={empty ? FIRST_PLACEHOLDER : PLACEHOLDERS[phase]}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={onKeyDown}
              />
              <button
                type="submit"
                className="st-send"
                aria-label="Send"
                disabled={state.busy || !input.trim()}
              >
                <svg className="st-ico" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M12 19V5M6 11l6-6 6 6" />
                </svg>
              </button>
            </div>
          </form>
        </div>

        {slug && (
          <Canvas
            ref={canvas}
            slug={slug}
            live={live}
            ready={claimed}
            view={view}
            addresses={addresses}
            onView={(next) => crossfade(() => setView(next))}
          />
        )}
      </div>

      {toast && (
        <div className="st-toast" role="status">
          {toast}
        </div>
      )}
    </section>
  );
}
