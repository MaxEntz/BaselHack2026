import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import Markdown, { type Components } from "react-markdown";
import { Link, useNavigate } from "react-router";
import type { ChatClient } from "../../chat/client.ts";
import type { ChatItem } from "../../chat/state.ts";
import { useChat } from "../../chat/useChat.ts";
import { composing } from "../../ime.ts";
import type { ConversationItem, LiveEvent, ManagePage } from "../../types.ts";
import { useClient, useLoad } from "../data.ts";
import { dayLong, dayShort } from "../dates.ts";
import { guests, throughAgent } from "../pages/home/format.ts";
import { PAGES, bookingPath, pagePath } from "../pages.ts";
import { Icon } from "../ui/index.ts";
import { chips } from "./chips.ts";

const PLACEHOLDER = "Ask anything about your restaurant";
const MAX_INPUT_PX = 130;
// The mockup's list shows the first 8; Reservations has the rest.
const LISTED = 8;
// A booking from then on today reads "tonight".
const EVENING = "17:00";
// The panel never shows an onboarding: its welcome is empty until the merchant writes.
const WELCOME = { onboarding: "", operations: "" };

// Under a link card, as the platform's `PAGE_LINKS` tells the model.
const PAGE_LINKS: Record<ManagePage, [string, string]> = {
  reservations: ["You can see them in", "."],
  guests: ["You can see them in", "."],
  hours: ["You can change this in", "."],
  floor: ["You can change this in", "."],
  rules: ["You can change this in", "."],
  agent: ["You can see what it did on the", " page."],
  settings: ["You can change this in", "."],
};

// The shell hands the panel the live events it receives: `listen(handler)`, `listen(null)`.
// A paragraph as a plain block: the mockup's replies are bare text in their bubble.
const PARAGRAPH: Components = { p: ({ node: _node, ...props }) => <div className="para" {...props} /> };

export type Listen = (handler: ((event: LiveEvent) => void) | null) => void;

/**
 * The assistant in the management site (#140): the platform's `/chat` on the owner's own
 * session, which it keeps, so a reload or another device finds the conversation.
 */
export function AssistantChat({
  slug,
  name,
  now,
  chat,
  live,
}: {
  slug: string;
  name: string;
  // The platform's clock (`MerchantOverview.now`), for the chips' days.
  now: string | null;
  chat: ChatClient;
  live: Listen;
}) {
  const client = useClient();
  const load = useCallback((signal: AbortSignal) => client.assistant(slug, signal), [client, slug]);
  const { data, error } = useLoad(load, 0, false);
  if (error) return <p className="asst-note">{error}</p>;
  if (!data) return <div id="feed" role="log" aria-label="Conversation" aria-busy="true" />;
  return (
    <Conversation
      key={data.session_id}
      slug={slug}
      name={name}
      now={now}
      chat={chat}
      live={live}
      sessionId={data.session_id}
      restore={data.items}
    />
  );
}

type ConversationProps = {
  slug: string;
  name: string;
  now: string | null;
  chat: ChatClient;
  live: Listen;
  sessionId: string;
  restore: readonly ConversationItem[];
};

function Conversation({ slug, name, now, chat, live, sessionId, restore }: ConversationProps) {
  const { state, send, notify } = useChat(chat, WELCOME, { slug, name }, { sessionId, restore, listen: false });
  const asked = new Set(state.items.flatMap((item) => (item.type === "user" ? [item.text] : [])));
  const feed = useRef<HTMLDivElement>(null);

  // The shell listens to the live events and hands them over.
  useEffect(() => {
    live(notify);
    return () => live(null);
  }, [live, notify]);

  useLayoutEffect(() => {
    const node = feed.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [state.items, state.activity]);

  const today = now?.slice(0, 10) ?? null;
  return (
    <>
      <div id="feed" ref={feed} role="log" aria-live="polite" aria-label="Conversation">
        {state.items.map((item) => (
          <Item key={item.id} item={item} slug={slug} today={today} />
        ))}
        {state.activity && <p className="msg bot asst-busy">{state.activity}</p>}
      </div>
      <AssistantComposer busy={state.busy} offered={now ? chips(now, asked) : []} onSend={(message) => void send(message)} />
    </>
  );
}

type AssistantComposerProps = {
  busy: boolean;
  // The chips: each one is sent as it reads.
  offered: readonly string[];
  onSend: (message: string) => void;
};

// The chips and the message box: Enter sends, Shift+Enter and an IME's Enter don't.
function AssistantComposer({ busy, offered, onSend }: AssistantComposerProps) {
  const [text, setText] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);

  const grow = () => {
    const node = input.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, MAX_INPUT_PX)}px`;
  };
  useLayoutEffect(grow, [text]);

  const submit = (message: string) => {
    const trimmed = message.trim();
    if (!trimmed || busy) return;
    setText("");
    onSend(trimmed);
  };
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    submit(text);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || composing(event)) return;
    event.preventDefault();
    submit(text);
  };

  return (
    <form className="composer" onSubmit={onSubmit}>
      <div id="chips">
        {offered.map((chip) => (
          <button key={chip} type="button" className="pill sm" disabled={busy} onClick={() => submit(chip)}>
            {chip}
          </button>
        ))}
      </div>
      <div className="box-in">
        <label className="sr" htmlFor="asst-input">
          {PLACEHOLDER}
        </label>
        <textarea
          id="asst-input"
          ref={input}
          rows={1}
          placeholder={PLACEHOLDER}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <button className="send" type="submit" aria-label="Send" disabled={!text.trim() || busy}>
          <Icon name="up" />
        </button>
      </div>
    </form>
  );
}

function Item({ item, slug, today }: { item: ChatItem; slug: string; today: string | null }) {
  switch (item.type) {
    case "user":
      return <div className="msg me">{item.text}</div>;
    case "assistant":
      return item.text ? <Bot text={item.text} /> : null;
    case "error":
      return (
        <div className="msg bot asst-error" role="alert">
          {item.message}
        </div>
      );
    case "notification":
      return <Ping event={item.event} today={today} />;
    case "steps":
      return null;
    case "card":
      return <Card card={item.card} slug={slug} />;
  }
}

function Bot({ text }: { text: string }) {
  return (
    <div className="msg bot">
      <Markdown allowedElements={["p", "strong", "em", "br", "ul", "ol", "li"]} unwrapDisallowed components={PARAGRAPH}>
        {text}
      </Markdown>
    </div>
  );
}

// "New booking through your agent / Kim Weber, 2 guests, tonight 20:00, T4".
function Ping({ event, today }: { event: LiveEvent; today: string | null }) {
  const { reservation: r, date } = event;
  const agent = throughAgent(event.via);
  const day = date !== today ? dayShort(date) : r.time >= EVENING ? "tonight" : "today";
  const what = { new_booking: "New booking", updated: "Booking moved", cancelled: "Booking cancelled" }[event.kind];
  return (
    <div className={agent ? "ping" : "ping you"}>
      <small>
        {what} {agent ? "through your agent" : "by you"}
      </small>
      <b className="num">
        {r.guest_name}, {guests(r.party_size)}, {day} {r.time}, {r.table_label}
      </b>
    </div>
  );
}

function Card({ card, slug }: { card: Extract<ChatItem, { type: "card" }>["card"]; slug: string }) {
  const navigate = useNavigate();
  switch (card.kind) {
    case "reservations": {
      const { date, reservations } = card.data;
      if (reservations.length === 0) return null;
      return (
        <div className="res-list">
          {reservations.slice(0, LISTED).map((r) => (
            <button key={r.id} type="button" className="num" onClick={() => navigate(bookingPath(slug, date, r.id))}>
              <span>{r.time}</span>
              <span>{r.guest_name}</span>
              <span>
                {r.party_size}, {r.table_label}
              </span>
            </button>
          ))}
          {reservations.length > LISTED && (
            <Link className="link" to={`${pagePath(slug, "reservations")}?date=${date}`}>
              All {reservations.length} in Reservations
            </Link>
          )}
        </div>
      );
    }
    case "stats": {
      const { date, guests: count, bookings, seats, blocked } = card.data;
      return (
        <div className="msg bot">
          {dayLong(date)}: <b>{guests(count)}</b> in {bookings === 1 ? "1 booking" : `${bookings} bookings`}, for {seats}{" "}
          seats.{blocked.length > 0 && ` Blocked: ${blocked.join(", ")}.`}
        </div>
      );
    }
    case "policy": {
      const { max_party_size, slot_length_min, services } = card.data;
      const open = services.map((s) => `${s.start}–${s.end}`).join(", ");
      return (
        <div className="msg bot">
          Groups up to <b>{max_party_size}</b>, a table for {slot_length_min / 60 === 2 ? "2 hours" : `${slot_length_min} minutes`}
          , open {open}.
        </div>
      );
    }
    case "confirmation":
      return <div className="msg bot">{card.data.text}</div>;
    case "diff": {
      const { label, before, after } = card.data;
      return (
        <div className="msg bot">
          {label}: {before && <s>{before}</s>} <b>{after}</b>
        </div>
      );
    }
    case "page": {
      const [lead, tail] = PAGE_LINKS[card.data.page];
      return (
        <div className="msg bot">
          {lead}{" "}
          <Link className="link" to={pagePath(slug, card.data.page)}>
            {PAGES[card.data.page]}
          </Link>
          {tail}
        </div>
      );
    }
    case "profile":
    case "view":
      return null;
  }
}
