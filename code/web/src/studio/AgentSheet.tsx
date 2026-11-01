import { type FormEvent, useEffect, useState } from "react";
import type { ManagementClient } from "../management/client.ts";
import { ASSISTANT } from "../strings.ts";
import type { AgentIntent, AgentRequest, AgentTryOutput, DaySlots, GeneratedTool, PendingAction, TalkOutput } from "../types.ts";
import type { Addresses } from "./feed.tsx";

// The intents that address no merchant: booking is a conversation with the agent (#254).
const INTENTS: readonly AgentIntent[] = ["DISCOVER", "RECALL", "PING"];
const PREVIEW_PARTY = 2;
const PREVIEW_SLOTS = 3;
const MAX_MESSAGE = 2000;
const YES = "Yes, please go ahead.";

// How a session opens (PROTOCOL.md §5). The token is the agent's own: it is never shown.
const HELLO = JSON.stringify({
  type: "hello",
  batp: "0.3",
  agent: {
    id: "urn:batp:agent:claude-gateway",
    name: `${ASSISTANT} via the Sésame gateway`,
    kind: "user-agent",
    operator: "merchant-agents",
  },
  token: "…",
});
const WELCOME = JSON.stringify({
  type: "welcome",
  batp: "0.3",
  node: { id: "urn:batp:node:merchant-agents", name: "Sésame" },
  intents: ["DISCOVER", "TALK", "RECALL", "PING"],
  session: "s-1",
});

function FrameLine({ out, text }: { out: boolean; text: string }) {
  // Only the frame's own status, as the mockup colours it: the first one.
  const match = text.match(/"status":(\d{3})/);
  const at = match?.index ?? -1;
  return (
    <div className={out ? "st-w-f out" : "st-w-f in"}>
      <span aria-hidden="true">{out ? "→" : "←"}</span>
      {match && at >= 0 ? (
        <code>
          {text.slice(0, at)}
          {'"status":'}
          <b className={match[1] === "200" ? "st-w-s ok" : "st-w-s bad"}>{match[1]}</b>
          {text.slice(at + match[0].length)}
        </code>
      ) : (
        <code>{text}</code>
      )}
    </div>
  );
}

// What an agent gets once the merchant publishes: the real times, in a TALK answer's shape. The
// reply stands for the model's, which words the same times its own way.
function previewFrames(slug: string, day: DaySlots): [string, string] {
  const free = day.slots.filter((slot) => slot.table && !slot.blocked).slice(0, PREVIEW_SLOTS);
  const times = free.map((slot) => slot.time);
  const request = {
    type: "request",
    id: "r-1",
    intent: "TALK",
    merchant: slug,
    params: { message: `A table for ${day.party_size} on ${day.date}?`, idempotency_key: "m-1" },
  };
  const response = {
    type: "response",
    id: "r-1",
    status: 200,
    result: {
      conversation: "c-1",
      reply: times.length > 0 ? `I can offer ${times.join(", ")}. Which name?` : "We're full that day.",
      slots: free.map((slot) => ({ starts_at: slot.starts_at, table_label: slot.table?.label })),
      pending: null,
      reservation: null,
      fallback_url: null,
      tools_called: ["get_availability"],
    },
  };
  return [JSON.stringify(request), JSON.stringify(response)];
}

type Client = Pick<ManagementClient, "agentRequests" | "slots" | "tryAgent" | "talkToAgent">;

type AgentSheetProps = {
  client: Client;
  slug: string;
  live: boolean;
  // The merchant's today, from the platform's clock; null until it is known.
  today: string | null;
  addresses: Addresses | null;
  tools: readonly GeneratedTool[];
  fallback: string | null;
};

// Live, today's requests from the node's log; not yet, today's times as an agent will get them.
function useTodaysFrames(client: Client, slug: string, live: boolean, today: string | null) {
  const [logged, setLogged] = useState<readonly AgentRequest[]>([]);
  const [preview, setPreview] = useState<DaySlots | null>(null);
  useEffect(() => {
    if (!today) return;
    const abort = new AbortController();
    if (live) {
      client
        .agentRequests(slug, { date: today, refused: false }, abort.signal)
        .then(({ requests }) => {
          if (!abort.signal.aborted) setLogged(requests);
        })
        .catch(() => {
          // No log to show yet: the Send buttons still work.
        });
    } else {
      client
        .slots(slug, { date: today, partySize: PREVIEW_PARTY }, abort.signal)
        .then((day) => {
          if (!abort.signal.aborted) setPreview(day);
        })
        .catch(() => {
          // The session then shows its opening frames only.
        });
    }
    return () => abort.abort();
  }, [client, slug, live, today]);
  return { logged, previewed: !live && preview ? previewFrames(slug, preview) : null };
}

// The Send buttons and the message box: one real frame each, through the node (#151, #253).
function useTryAgent(client: Client, slug: string) {
  const [sent, setSent] = useState<readonly AgentTryOutput[]>([]);
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [conversation, setConversation] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction | null>(null);
  async function send(intent: AgentIntent) {
    setSending(true);
    setProblem(null);
    try {
      const frames = await client.tryAgent(slug, intent);
      setSent((earlier) => [...earlier, frames]);
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "That frame couldn't be sent.");
    } finally {
      setSending(false);
    }
  }
  async function talk(message: string, confirm: string | null): Promise<boolean> {
    setSending(true);
    setProblem(null);
    try {
      const frames = await client.talkToAgent(slug, { message, conversation, confirm });
      setSent((earlier) => [...earlier, frames]);
      if (frames.talk) {
        setConversation(frames.talk.conversation);
        setPending(frames.talk.pending);
      } else if (frames.status === 404) {
        // The conversation has ended, or the agent is hidden: the next message starts a new one.
        setConversation(null);
        setPending(null);
      }
      // Any other refusal says nothing about the proposal: it still lives on the server.
      return frames.status === 200;
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "That message couldn't be sent.");
      return false;
    } finally {
      setSending(false);
    }
  }
  return { sent, sending, problem, pending, send, talk };
}

function TalkReply({ talk }: { talk: TalkOutput }) {
  return (
    <div className="st-w-reply">
      <p>{talk.reply}</p>
      {talk.tools_called.length > 0 && (
        <small>
          Tools it called:{" "}
          {talk.tools_called.map((tool) => (
            <code key={tool}>{tool}</code>
          ))}
        </small>
      )}
    </div>
  );
}

// What Confirm really does, by the proposal's action: each one writes for real.
const CONFIRM_DOES: Record<PendingAction["action"], string> = {
  book: "Confirm makes a real booking: you can cancel it from Reservations.",
  change: "Confirm really moves the booking.",
  cancel: "Confirm really cancels the booking.",
};

// `ask` answers whether the message went. The box empties at once, so that a question typed
// while the agent answers isn't wiped; if the message didn't go, the owner gets it back.
function AskBox({ sending, ask }: { sending: boolean; ask: (message: string) => Promise<boolean> }) {
  const [draft, setDraft] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    // A pasted tab or control character is no part of a question, and the protocol refuses it.
    const message = draft.replace(/\p{Cc}/gu, " ").trim();
    if (!message) return;
    const typed = draft;
    setDraft("");
    if (!(await ask(message))) setDraft((current) => current || typed);
  }

  return (
    <form className="st-w-talk" onSubmit={(event) => void submit(event)}>
      <label htmlFor="st-w-message">Ask your agent</label>
      <input
        id="st-w-message"
        value={draft}
        maxLength={MAX_MESSAGE}
        placeholder="Do you have vegetarian dishes?"
        onChange={(event) => setDraft(event.target.value)}
      />
      <button type="submit" disabled={sending || !draft.trim()}>
        Ask
      </button>
    </form>
  );
}

/** The agent site has no pages: this is what agents exchange with it (#151). */
export function AgentSheet({ client, slug, live, today, addresses, tools, fallback }: AgentSheetProps) {
  const { logged, previewed } = useTodaysFrames(client, slug, live, today);
  const { sent, sending, problem, pending, send, talk } = useTryAgent(client, slug);

  return (
    <div className="st-w">
      <div className="st-w-top">
        <code className="st-w-addr">{addresses?.agent ?? `batp://…/${slug}`}</code>
        <span className={live ? "st-w-live on" : "st-w-live"}>{live ? "Live" : "Not live"}</span>
      </div>
      <p className="st-w-note">
        No pages, no buttons, no design. Agents open a session on port 4710 and exchange one JSON frame
        per line, and an AI agent answers them in natural language, inside those frames. This is the
        whole site.
      </p>
      {!live && (
        <p className="st-w-warn">
          Until you publish, your agent answers every message with a 404. The first answer below is
          what agents get once you publish.
        </p>
      )}
      <section aria-label="Protocol session">
        <h3>A session with your agent</h3>
        <div className="st-w-log" role="log" aria-label="Frames exchanged with your agent">
          <FrameLine out text={HELLO} />
          <FrameLine out={false} text={WELCOME} />
          {previewed && (
            <>
              <FrameLine out text={previewed[0]} />
              <FrameLine out={false} text={previewed[1]} />
            </>
          )}
          {logged.toReversed().map((request) => (
            <div key={request.id} className="st-w-pair">
              <FrameLine out text={request.request} />
              <FrameLine out={false} text={request.response} />
            </div>
          ))}
          {sent.map((frames, index) => (
            <div key={index} className="st-w-pair">
              <FrameLine out text={frames.request} />
              <FrameLine out={false} text={frames.response} />
              {frames.talk && <TalkReply talk={frames.talk} />}
            </div>
          ))}
        </div>
        {pending && (
          <div className="st-w-confirm" role="group" aria-label="Your agent is waiting for a yes">
            <p>
              {pending.summary} <small>{CONFIRM_DOES[pending.action]}</small>
            </p>
            <button type="button" disabled={sending} onClick={() => void talk(YES, pending.id)}>
              Confirm
            </button>
          </div>
        )}
        <AskBox sending={sending} ask={(message) => talk(message, null)} />
        <div className="st-w-send" role="group" aria-label="Send a request">
          <span>Send</span>
          {INTENTS.map((intent) => (
            <button key={intent} type="button" disabled={sending} onClick={() => void send(intent)}>
              {intent}
            </button>
          ))}
        </div>
        {problem && (
          <p className="st-w-problem" role="alert">
            {problem}
          </p>
        )}
      </section>
      <Bridge addresses={addresses} tools={tools} fallback={fallback} />
    </div>
  );
}

type BridgeProps = Pick<AgentSheetProps, "addresses" | "tools" | "fallback">;

function Bridge({ addresses, tools, fallback }: BridgeProps) {
  return (
    <section aria-label="MCP bridge">
      <h3>What {ASSISTANT} reads through the MCP bridge</h3>
      {addresses && <code className="st-w-bridge">{addresses.mcp}</code>}
      {tools.length > 0 && (
        <ul className="st-w-tools">
          {tools.map((tool) => (
            <li key={tool.name}>
              <code>{tool.name}</code>
              <span>{tool.description}</span>
            </li>
          ))}
        </ul>
      )}
      {fallback && (
        <p className="st-w-fb">
          When the agent can't answer, the guest gets your own page: <code>{fallback}</code>
        </p>
      )}
    </section>
  );
}
