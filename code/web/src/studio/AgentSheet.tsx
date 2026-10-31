import { useEffect, useState } from "react";
import type { ManagementClient } from "../management/client.ts";
import { ASSISTANT } from "../strings.ts";
import type { AgentIntent, AgentRequest, AgentTryOutput, DaySlots, GeneratedTool } from "../types.ts";
import type { Addresses } from "./feed.tsx";

const INTENTS: readonly AgentIntent[] = ["DISCOVER", "AVAILABILITY", "BOOK", "AMEND", "CANCEL", "RECALL", "PING"];
const PREVIEW_PARTY = 2;
const PREVIEW_SLOTS = 3;

// How a session opens (PROTOCOL.md §5). The token is the agent's own: it is never shown.
const HELLO = JSON.stringify({
  type: "hello",
  batp: "0.1",
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
  batp: "0.1",
  node: { id: "urn:batp:node:merchant-agents", name: "Sésame" },
  intents: INTENTS,
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

// What an agent gets once the merchant publishes: the real times, in the frame's shape.
function previewFrames(slug: string, day: DaySlots): [string, string] {
  const free = day.slots.filter((slot) => slot.table && !slot.blocked).slice(0, PREVIEW_SLOTS);
  const request = {
    type: "request",
    id: "r-1",
    intent: "AVAILABILITY",
    merchant: slug,
    params: { date: day.date, party_size: day.party_size },
  };
  const response = {
    type: "response",
    id: "r-1",
    status: 200,
    result: { slots: free.map((slot) => ({ starts_at: slot.starts_at, table_label: slot.table?.label })) },
  };
  return [JSON.stringify(request), JSON.stringify(response)];
}

type AgentSheetProps = {
  client: Pick<ManagementClient, "agentRequests" | "slots" | "tryAgent">;
  slug: string;
  live: boolean;
  // The merchant's today, from the platform's clock; null until it is known.
  today: string | null;
  addresses: Addresses | null;
  tools: readonly GeneratedTool[];
  fallback: string | null;
};

/** The agent site has no pages: this is what agents exchange with it (#151). */
export function AgentSheet({ client, slug, live, today, addresses, tools, fallback }: AgentSheetProps) {
  const [logged, setLogged] = useState<readonly AgentRequest[]>([]);
  const [preview, setPreview] = useState<DaySlots | null>(null);
  const [sent, setSent] = useState<readonly AgentTryOutput[]>([]);
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

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

  const previewed = !live && preview ? previewFrames(slug, preview) : null;

  return (
    <div className="st-w">
      <div className="st-w-top">
        <code className="st-w-addr">{addresses?.agent ?? `batp://…/${slug}`}</code>
        <span className={live ? "st-w-live on" : "st-w-live"}>{live ? "Live" : "Not live"}</span>
      </div>
      <p className="st-w-note">
        No pages, no buttons, no design. Agents open a session on port 4710 and exchange one JSON frame
        per line. This is the whole site.
      </p>
      {!live && (
        <p className="st-w-warn">
          Right now every agent gets a 404. The first answer below is what they get once you publish.
        </p>
      )}
      <section aria-label="Protocol session">
        <h3>A session with your agent</h3>
        <div className="st-w-log">
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
            </div>
          ))}
        </div>
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
    </div>
  );
}
