import { useLayoutEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import type { Components } from "react-markdown";
import { VIA_LABELS, describeDate, formatServices, notificationTitle, plural } from "../cards/format.ts";
import type { StepName, StepState } from "../chat/state.ts";
import type { BuildAgentOutput, BuildFact, LiveEvent } from "../types.ts";
import { Hello } from "./Hello.tsx";
import { type CanvasView, type FeedEntry, stepNote } from "./phase.ts";

// Links in replies open next to the chat, never in place of it.
const MARKDOWN: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
};

export type View = CanvasView;

export type Addresses = { agent: string; mcp: string; management: string };

const STEPS: readonly { name: StepName; title: string }[] = [
  { name: "crawl", title: "Reading your site" },
  { name: "extract", title: "Filling your profile" },
  { name: "validate", title: "Checking it" },
  { name: "provision", title: "Setting up your bookings" },
  { name: "deploy", title: "Going live" },
];

const STEP_CLASS: Record<StepState["status"], string> = {
  running: "run",
  done: "done",
  waiting: "wait",
  error: "failed",
};

// These cards show the progress themselves.
const CARRIES_ON = new Set<string>(["steps", "review"]);

const CheckIcon = () => (
  <svg className="st-ico" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </svg>
);

const CopyIcon = () => (
  <svg className="st-ico" viewBox="0 0 24 24" aria-hidden="true">
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15V6a2 2 0 0 1 2-2h9" />
  </svg>
);

type FeedProps = {
  // Before the first message: the first question, in the same log, so that the first reply
  // is announced (a log added with its content already in it is not).
  empty: boolean;
  entries: readonly FeedEntry[];
  activity: string | null;
  busy: boolean;
  addresses: Addresses | null;
  onOpen: (view: View) => void;
  onFact: (fact: BuildFact) => void;
  onCopy: (text: string) => void;
  onRetry: () => void;
};

export function Feed({ empty, entries, activity, busy, addresses, onOpen, onFact, onCopy, onRetry }: FeedProps) {
  const feed = useRef<HTMLDivElement>(null);
  const lastError = entries.findLast((entry) => entry.kind === "error")?.key;
  // Smooth when a message comes in; text that streams into the last one just follows.
  const count = entries.length;
  const seen = useRef(count);
  const last = entries.at(-1);
  const tail = last?.kind === "bot" ? last.text.length : 0;
  useLayoutEffect(() => {
    const element = feed.current;
    if (!element) return;
    const behavior: ScrollBehavior = count !== seen.current ? "smooth" : "auto";
    seen.current = count;
    element.scrollTo?.({ top: element.scrollHeight, behavior });
  }, [count, tail, busy, activity]);

  return (
    <div ref={feed} className="st-feed" role="log" aria-live="polite" aria-label="Conversation">
      {empty && <Hello />}
      {!empty && entries.map((entry) => (
        <Entry
          key={entry.key}
          entry={entry}
          addresses={addresses}
          onOpen={onOpen}
          onFact={onFact}
          onCopy={onCopy}
          onRetry={entry.key === lastError && !busy ? onRetry : undefined}
        />
      ))}
      {busy && !CARRIES_ON.has(last?.kind ?? "") && (
        <p className="st-thinking">{activity ?? "Thinking…"}</p>
      )}
    </div>
  );
}

type EntryProps = {
  entry: FeedEntry;
  addresses: Addresses | null;
  onOpen: (view: View) => void;
  onFact: (fact: BuildFact) => void;
  onCopy: (text: string) => void;
  onRetry?: () => void;
};

function Entry({ entry, addresses, onOpen, onFact, onCopy, onRetry }: EntryProps) {
  switch (entry.kind) {
    case "user":
      return (
        <div className={entry.first ? "st-msg me first" : "st-msg me"}>{entry.text}</div>
      );
    case "bot":
      return (
        <div className="st-msg bot">
          <Markdown components={MARKDOWN}>{entry.text}</Markdown>
        </div>
      );
    case "steps":
      return (
        <div className="st-msg bot">
          <StepsCard title={entry.title} steps={entry.steps} found={entry.found} />
        </div>
      );
    case "review":
      return <Review data={entry.data} pages={entry.pages} loginUrl={entry.loginUrl} onOpen={onOpen} onFact={onFact} onCopy={onCopy} />;
    case "moment":
      return (
        <div className="st-msg bot">
          <p>{entry.text}</p>
        </div>
      );
    case "live":
      return (
        <>
          <div className="st-msg bot">
            <p>
              You're live. Agents get real answers now. The sample bookings are gone, so your list
              stays empty until the first guest books.
            </p>
          </div>
          {addresses && (
            <div className="st-msg bot">
              <AddressesCard addresses={addresses} onCopy={onCopy} />
            </div>
          )}
        </>
      );
    case "card":
      return (
        <div className="st-msg bot">
          <ResultCard card={entry.card} onOpen={onOpen} />
        </div>
      );
    case "ping":
      return (
        <div className="st-msg bot">
          <Ping event={entry.event} />
        </div>
      );
    case "error":
      return (
        <div className="st-msg bot st-error" role="alert">
          <p>{entry.message}</p>
          {onRetry && (
            <button type="button" className="st-chip" onClick={onRetry}>
              Try again
            </button>
          )}
        </div>
      );
  }
}

function StepsCard({ title, steps, found }: { title: string; steps: Extract<FeedEntry, { kind: "steps" }>["steps"]; found: number | null }) {
  return (
    <div className="st-card" aria-label="Build progress">
      <h3>{title}</h3>
      {STEPS.map(({ name, title: stepTitle }) => {
        const step = steps[name];
        const note = step ? stepNote(name, step, found) : null;
        return (
          <div key={name} className={`st-step ${step ? STEP_CLASS[step.status] : ""}`}>
            <span className="st-tick">{step?.status === "done" && <CheckIcon />}</span>
            <div>
              <b>{stepTitle}</b> <code>{name}</code>
            </div>
            <p>{note}</p>
          </div>
        );
      })}
    </div>
  );
}

type ReviewProps = {
  data: BuildAgentOutput;
  pages: number | null;
  loginUrl: string | null;
  onOpen: (view: View) => void;
  onFact: (fact: BuildFact) => void;
  onCopy: (text: string) => void;
};

function Review({ data, pages, loginUrl, onOpen, onFact, onCopy }: ReviewProps) {
  const [pressed, setPressed] = useState<string | null>(null);
  return (
    <>
      <div className="st-msg bot">
        <p>
          {pages !== null && pages > 1 ? `I read ${pages} pages` : "I read your page"}. Tap a line to
          see where each detail came from.
        </p>
      </div>
      <div className="st-msg bot">
        <div className="st-card">
          <h3>What went into your profile</h3>
          {data.facts.map((fact) => (
            <button
              key={fact.key}
              type="button"
              className="st-fact"
              aria-pressed={pressed === fact.key}
              onClick={() => {
                setPressed(fact.key);
                onFact(fact);
              }}
            >
              <small>{fact.label}</small>
              <span>{fact.value}</span>
              <em>{fact.source ?? "default"}</em>
            </button>
          ))}
        </div>
      </div>
      {data.flags.length > 0 && (
        <div className="st-msg bot">
          <div className="st-card">
            <h3>Check these</h3>
            {data.flags.map((flag) => (
              <div key={flag.title} className="st-flag">
                <b>{flag.title}</b>
                <span>{flag.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="st-msg bot">
        <p>
          Both sites are built and only you can see them. Your agent site has no pages: it answers
          agents in JSON, and you can watch it do that. The bookings on your management site are
          samples, and they go away when you publish. Ask for anything you'd change.
        </p>
      </div>
      <div className="st-msg bot">
        <div className="st-opens">
          <button type="button" className="st-open-site" onClick={() => onOpen("agent")}>
            <span className="st-tile" aria-hidden="true">
              Ag
            </span>
            <b>Agent site</b>
          </button>
          <button type="button" className="st-open-site" onClick={() => onOpen("manage")}>
            <span className="st-tile" aria-hidden="true">
              Mg
            </span>
            <b>Management site</b>
          </button>
        </div>
      </div>
      {loginUrl && (
        <div className="st-msg bot">
          <div className="st-card">
            <h3>Your private link: keep it, it is shown only once.</h3>
            <UrlRow
              label="It opens your management site from any browser. Anyone with it can manage your bookings."
              url={loginUrl}
              copyLabel="Copy your private link"
              onCopy={onCopy}
            />
          </div>
        </div>
      )}
    </>
  );
}

function AddressesCard({ addresses, onCopy }: { addresses: Addresses; onCopy: (text: string) => void }) {
  const rows: [string, string][] = [
    ["Agent site, for any agent", addresses.agent],
    ["Agent site, for Claude", addresses.mcp],
    ["Management site", addresses.management],
  ];
  return (
    <div className="st-card">
      <h3>Your addresses</h3>
      {rows.map(([label, url]) => (
        <UrlRow key={label} label={label} url={url} copyLabel={`Copy ${label}`} onCopy={onCopy} />
      ))}
    </div>
  );
}

type UrlRowProps = { label: string; url: string; copyLabel: string; onCopy: (text: string) => void };

function UrlRow({ label, url, copyLabel, onCopy }: UrlRowProps) {
  return (
    <div className="st-url">
      <small>{label}</small>
      <code>{url}</code>
      <button type="button" aria-label={copyLabel} onClick={() => onCopy(url)}>
        <CopyIcon />
      </button>
    </div>
  );
}

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

type ResultCardData = Extract<FeedEntry, { kind: "card" }>["card"];
type CardData<K extends ResultCardData["kind"]> = Extract<ResultCardData, { kind: K }>["data"];

function ResultCard({ card, onOpen }: { card: ResultCardData; onOpen: (view: View) => void }) {
  switch (card.kind) {
    case "diff":
      return <DiffResult data={card.data} onOpen={onOpen} />;
    case "view":
    case "page":
      return null;
    case "reservations":
      return <ReservationsResult data={card.data} />;
    case "stats":
      return <StatsResult data={card.data} />;
    case "policy":
      return (
        <div className="st-card">
          <div className="st-diff">
            <span>Largest group</span>
            <b>{card.data.max_party_size}</b>
          </div>
          <div className="st-diff">
            <span>Hours</span>
            <b>{formatServices(card.data.services)}</b>
          </div>
        </div>
      );
    case "confirmation":
      return (
        <div className="st-card">
          <div className="st-diff">
            <span>{card.data.text}</span>
          </div>
        </div>
      );
  }
}

function DiffResult({ data, onOpen }: { data: CardData<"diff">; onOpen: (view: View) => void }) {
  const { label, before, after, view } = data;
  return (
    <div className="st-card">
      <div className="st-diff">
        <span>{label}</span>
        {before && <s>{before}</s>}
        <b>{after}</b>
        {view && (
          <button type="button" className="st-see" onClick={() => onOpen(view)}>
            See it
          </button>
        )}
      </div>
    </div>
  );
}

function ReservationsResult({ data }: { data: CardData<"reservations"> }) {
  const when = describeDate(data.date);
  const rows = data.reservations
    .filter((row) => row.status === "confirmed")
    .toSorted((a, b) => a.time.localeCompare(b.time));
  if (rows.length === 0) {
    return (
      <div className="st-card">
        <h3>No bookings {when} yet</h3>
      </div>
    );
  }
  return (
    <div className="st-card">
      <h3>Bookings {when}</h3>
      <table className="st-tbl">
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.time}</td>
              <td>{row.guest_name}</td>
              <td>{row.party_size}</td>
              <td>{row.table_label}</td>
              <td>{VIA_LABELS[row.via]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StatsResult({ data }: { data: CardData<"stats"> }) {
  return (
    <div className="st-card">
      <h3>{capitalize(describeDate(data.date))}</h3>
      <div className="st-nums">
        <div>
          <b>{data.bookings}</b>
          <small>bookings</small>
        </div>
        <div>
          <b>{data.guests}</b>
          <small>guests</small>
        </div>
        <div>
          <b>{data.seats}</b>
          <small>seats</small>
        </div>
      </div>
    </div>
  );
}

function Ping({ event }: { event: LiveEvent }) {
  const { reservation } = event;
  return (
    <div className="st-ping" role="status">
      <small>{notificationTitle(event)}</small>
      <b>
        {reservation.guest_name}, {reservation.time}, {plural(reservation.party_size, "guest")},{" "}
        {reservation.table_label}
      </b>
    </div>
  );
}
