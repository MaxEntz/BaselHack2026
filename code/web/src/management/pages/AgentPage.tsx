import { useCallback, useState } from "react";
import type { AgentRequest, MerchantStatus } from "../../types.ts";
import { useClient, useLoad } from "../data.ts";
import { PAGES } from "../pages.ts";
import { errorMessage } from "../problems.ts";
import { Pill, useToast } from "../ui/index.ts";
import { PageTitle } from "./PageTitle.tsx";

export const PAUSED_TOAST = "The agent is paused. Guests get your booking page.";
export const RESUMED_TOAST = "The agent is live again.";

const STATUS: Record<MerchantStatus, string> = {
  live: "Live, and taking bookings.",
  down: "Paused. Guests get your booking page.",
  // A build waits for its owner (#136): publishing goes through the assistant, not this switch.
  building: "Not published yet. Ask the assistant to publish it.",
};

type Filter = "all" | "refused";

/**
 * What the merchant sees of their agent, which has no pages (#147): its status and the switch,
 * its addresses, and the requests other agents sent today with their frames. Read again on each
 * live event (`version`); the rows the merchant opened stay open.
 */
export function AgentPage({ slug, version }: { slug: string; version: number }) {
  const client = useClient();
  const toast = useToast();
  const loadMerchant = useCallback((signal: AbortSignal) => client.merchant(slug, signal), [client, slug]);
  const merchant = useLoad(loadMerchant, version);
  const [busy, setBusy] = useState(false);

  async function toggle(status: MerchantStatus) {
    setBusy(true);
    try {
      const answer = status === "down" ? await client.resumeAgent(slug) : await client.pauseAgent(slug);
      merchant.update((data) => ({ ...data, status: answer.status }));
      toast(answer.status === "down" ? PAUSED_TOAST : RESUMED_TOAST);
    } catch (error) {
      toast(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      toast("Copied");
    } catch {
      toast(`Copy this: ${value}`);
    }
  }

  const data = merchant.data;
  return (
    <>
      <PageTitle title={PAGES.agent}>
        Your agent has no pages and no design. Other agents connect to its address and book in JSON, and
        Claude comes through our bridge.
      </PageTitle>
      {merchant.error && (
        <p className="err" role="alert">
          {merchant.error}
        </p>
      )}
      {data && (
        <section className="ag on-dark" aria-label="Your agent">
          <div className="bar-row">
            <span className={data.status === "live" ? "status" : "status off"}>
              <i />
              {STATUS[data.status]}
            </span>
            {data.status !== "building" && (
              <Pill small className="right" disabled={busy} onClick={() => void toggle(data.status)}>
                {data.status === "down" ? "Resume the agent" : "Pause the agent"}
              </Pill>
            )}
          </div>
          <Address label="Its address" value={data.agent_address} onCopy={copy} />
          <Address label="For Claude" value={data.mcp_url} onCopy={copy} />
          <Requests slug={slug} today={data.now.slice(0, 10)} version={version} />
        </section>
      )}
    </>
  );
}

function Address({ label, value, onCopy }: { label: string; value: string; onCopy: (value: string) => void }) {
  return (
    <div className="addr">
      <span>{label}</span>
      <div>
        <code>{value}</code>
        <Pill small aria-label={`Copy ${label.toLowerCase()}`} onClick={() => onCopy(value)}>
          Copy
        </Pill>
      </div>
    </div>
  );
}

function Requests({ slug, today, version }: { slug: string; today: string; version: number }) {
  const client = useClient();
  const [filter, setFilter] = useState<Filter>("all");
  // Ids of the rows the merchant opened: kept when the list is read again.
  const [open, setOpen] = useState<ReadonlySet<number>>(new Set());
  const load = useCallback(
    (signal: AbortSignal) => client.agentRequests(slug, { date: today, refused: filter === "refused" }, signal),
    [client, slug, today, filter],
  );
  const { data, error } = useLoad(load, version);

  function toggled(id: number, isOpen: boolean) {
    setOpen((ids) => {
      if (ids.has(id) === isOpen) return ids;
      const next = new Set(ids);
      if (isOpen) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  return (
    <>
      <div className="m-hd ag-hd">
        <h2 className="dsp">What it did today</h2>
        <div className="pills" role="group" aria-label="Filter">
          <Pill small aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
            Everything
          </Pill>
          <Pill small aria-pressed={filter === "refused"} onClick={() => setFilter("refused")}>
            Refused
          </Pill>
        </div>
      </div>
      {error && (
        <p className="err" role="alert">
          {error}
        </p>
      )}
      {data && (
        <div>
          {data.requests.length ? (
            data.requests.map((request) => (
              <Request key={request.id} request={request} open={open.has(request.id)} onToggle={toggled} />
            ))
          ) : (
            <p className="ag-none">{filter === "refused" ? "Nothing refused today." : "No requests yet today."}</p>
          )}
        </div>
      )}
    </>
  );
}

function Request({
  request,
  open,
  onToggle,
}: {
  request: AgentRequest;
  open: boolean;
  onToggle: (id: number, open: boolean) => void;
}) {
  return (
    <details
      className={request.refused ? "item ref" : "item"}
      open={open}
      onToggle={(event) => onToggle(request.id, event.currentTarget.open)}
    >
      <summary>
        {/* `at` is the merchant's local time, with its offset. */}
        <time dateTime={request.at}>{request.at.slice(11, 16)}</time>
        <b>{request.title}</b>
        <u>See the request</u>
        {request.text && <p>{request.text}</p>}
      </summary>
      {/* The frames as sent: never re-serialized (#129). */}
      <pre>{`→ ${request.request}\n← ${request.response}`}</pre>
    </details>
  );
}
