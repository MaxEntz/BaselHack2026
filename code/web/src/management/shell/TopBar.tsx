import { type KeyboardEvent, type RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { now, useNow } from "../../clock.ts";
import type { ManagedReservation, MerchantOverview } from "../../types.ts";
import { useClient, useLoad } from "../data.ts";
import { dayShort } from "../dates.ts";
import { useLatestVersion } from "../live.ts";
import { NO_ACTIVITY } from "../pages/home/HomePage.tsx";
import { PAGES, type PageId, bookingPath, pagePath } from "../pages.ts";
import { Blob, Icon } from "../ui/index.ts";
import { browserPlatform, clockLabel, initials, notificationLine, pageHits, shortcutLabel } from "./format.ts";

export const NOTHING_FOUND = "Nothing by that name in the next 7 days.";
const BOOKING_HITS = 8;
const POPOVER_ITEMS = 5;

/** The sticky bar over every page but Assistant: search, clock, bell, avatar. */
export function TopBar({
  slug,
  page,
  merchant,
  unseen,
  onSeen,
}: {
  slug: string;
  page: PageId;
  merchant: MerchantOverview | null;
  unseen: number;
  onSeen: () => void;
}) {
  // The page the popover was opened on: changing page closes it.
  const [popOn, setPopOn] = useState<PageId | null>(null);
  const popOpen = popOn === page;
  const bell = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!popOpen) return;
    const outside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!bell.current?.contains(target) && !pop.current?.contains(target)) setPopOn(null);
    };
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setPopOn(null);
    };
    document.addEventListener("click", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("click", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [popOpen]);

  return (
    <header className="top">
      <Search slug={slug} />
      <div className="top-r">
        {merchant && <Clock merchant={merchant} />}
        <button
          ref={bell}
          className="bell"
          type="button"
          aria-label="Notifications"
          aria-expanded={popOpen}
          onClick={() => {
            setPopOn(popOpen ? null : page);
            onSeen();
          }}
        >
          <Icon name="bell" />
          {unseen > 0 && <i>{unseen}</i>}
        </button>
        {merchant && <Avatar name={merchant.owner_name?.trim() || merchant.name} />}
      </div>
      {popOpen && (
        <div className="pop" ref={pop}>
          <Notifications slug={slug} />
        </div>
      )}
    </header>
  );
}

type Hit = { kind: "page"; page: PageId } | { kind: "booking"; booking: ManagedReservation };

/** Pages by name, then up to 8 bookings of the next 7 days by guest or reference. */
function Search({ slug }: { slug: string }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const shortcut = useMemo(() => shortcutLabel(browserPlatform()), []);
  const wanted = query.trim();
  const bookings = useBookingHits(slug, wanted);
  useShortcut(input);
  useClickOutside(box, open, setOpen);

  const hits: Hit[] = [
    ...pageHits(wanted).map((page): Hit => ({ kind: "page", page })),
    ...(bookings ?? []).map((booking): Hit => ({ kind: "booking", booking })),
  ];
  const empty = bookings !== null && hits.length === 0;
  const shown = open && wanted !== "" && (hits.length > 0 || empty);

  function choose(hit: Hit) {
    setQuery("");
    setOpen(false);
    input.current?.blur();
    if (hit.kind === "page") navigate(pagePath(slug, hit.page), { viewTransition: true });
    else navigate(bookingPath(slug, hit.booking.date, hit.booking.id));
  }

  function keys(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") setOpen(false);
    if (event.key === "Enter") {
      event.preventDefault();
      if (hits[0]) choose(hits[0]);
    }
  }

  return (
    <div className="search" ref={box}>
      <Icon name="search" />
      <label className="sr" htmlFor="search">
        Search a guest, a booking or a page
      </label>
      <input
        ref={input}
        id="search"
        type="text"
        placeholder="Search a guest, a booking, a page"
        autoComplete="off"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={keys}
      />
      <kbd>{shortcut}</kbd>
      {shown && <Hits hits={hits} empty={empty} onChoose={choose} />}
    </div>
  );
}

// The bookings the platform found for `wanted`, only once it answered this very query, so an
// older answer never shows; null until then.
function useBookingHits(slug: string, wanted: string): ManagedReservation[] | null {
  const client = useClient();
  const [answer, setAnswer] = useState<{ query: string; bookings: ManagedReservation[] } | null>(null);
  useEffect(() => {
    if (!wanted) return;
    const abort = new AbortController();
    client.search(slug, wanted, abort.signal).then(
      (found) => setAnswer({ query: wanted, bookings: found.reservations.slice(0, BOOKING_HITS) }),
      () => {
        if (!abort.signal.aborted) setAnswer({ query: wanted, bookings: [] });
      },
    );
    return () => abort.abort();
  }, [client, slug, wanted]);
  return answer?.query === wanted ? answer.bookings : null;
}

// Cmd+K (Ctrl+K outside macOS) from anywhere on the site.
function useShortcut(input: RefObject<HTMLInputElement | null>) {
  useEffect(() => {
    const focus = (event: globalThis.KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        input.current?.focus();
        input.current?.select();
      }
    };
    document.addEventListener("keydown", focus);
    return () => document.removeEventListener("keydown", focus);
  }, [input]);
}

// A click outside `box` closes what is open.
function useClickOutside(box: RefObject<HTMLElement | null>, open: boolean, setOpen: (open: boolean) => void) {
  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("click", outside);
    return () => document.removeEventListener("click", outside);
  }, [box, open, setOpen]);
}

function Hits({ hits, empty, onChoose }: { hits: readonly Hit[]; empty: boolean; onChoose: (hit: Hit) => void }) {
  return (
    <div className="hits" role="group" aria-label="Search results">
      {hits.map((hit) =>
        hit.kind === "page" ? (
          <button key={`page-${hit.page}`} type="button" onClick={() => onChoose(hit)}>
            <span>{PAGES[hit.page]}</span>
            <span>Page</span>
          </button>
        ) : (
          <button
            key={hit.booking.id}
            type="button"
            className={hit.booking.status === "cancelled" ? "off" : undefined}
            onClick={() => onChoose(hit)}
          >
            <span>
              {hit.booking.guest_name}, {hit.booking.party_size}
              {hit.booking.status === "cancelled" && <em>, cancelled</em>}
            </span>
            <span className="num">
              {dayShort(hit.booking.date)} {hit.booking.time}
            </span>
          </button>
        ),
      )}
      {empty && <p>{NOTHING_FOUND}</p>}
    </div>
  );
}

/** "Sat 31 Oct, 19:40": the platform's clock (`MerchantOverview.now`), running, in the merchant's zone. */
function Clock({ merchant }: { merchant: MerchantOverview }) {
  // How far the platform's clock is from this browser's, measured when the overview arrived.
  const [skew] = useState(() => Date.parse(merchant.now) - now().getTime());
  const at = useNow(1000);
  return <span className="clock">{clockLabel(new Date(at.getTime() + skew), merchant.timezone)}</span>;
}

function Avatar({ name }: { name: string }) {
  return (
    <span className="avatar" role="img" aria-label={name}>
      <Blob index={1} />
      <span aria-hidden="true">{initials(name)}</span>
    </span>
  );
}

/** The latest 5 events, read when the popover opens and again on every change. */
function Notifications({ slug }: { slug: string }) {
  const client = useClient();
  const version = useLatestVersion();
  const load = useCallback((signal: AbortSignal) => client.dashboard(slug, signal), [client, slug]);
  const { data, error } = useLoad(load, version, false);
  const items = data?.activity.slice(0, POPOVER_ITEMS);
  return (
    <>
      <h3>Notifications</h3>
      {error && <p role="alert">{error}</p>}
      {items && (items.length ? items.map((item) => <p key={item.id}>{notificationLine(item)}</p>) : <p>{NO_ACTIVITY}</p>)}
    </>
  );
}
