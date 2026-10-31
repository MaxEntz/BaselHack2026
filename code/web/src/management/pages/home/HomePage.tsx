import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useNow } from "../../../clock.ts";
import type {
  ActivityItem,
  Dashboard,
  ManagedReservation,
  MerchantOverview,
  ReservationDay,
} from "../../../types.ts";
import type { ManagementClient } from "../../client.ts";
import { useClient } from "../../data.ts";
import { crossfade } from "../../motion.ts";
import { sampleBookings } from "../../samples.ts";
import { Blob, Box, Pill, Source, Tag, Under } from "../../ui/index.ts";
import { GuestsCard } from "./GuestsCard.tsx";
import {
  KIND,
  agentShare,
  bookings,
  clockOf,
  count,
  dayShort,
  firstName,
  greeting,
  guests,
  hourIn,
  minutes,
  subtitle,
  weekday,
} from "./format.ts";
import "./home.css";

export const NOTHING_TONIGHT = "Nothing booked for tonight yet.";
export const NO_ACTIVITY = "Nothing has happened yet.";
export const SAMPLES_NOTE = "Sample bookings: your agent is hidden from guests until you publish it.";
const TONIGHT_ROWS = 9;
const ACTIVITY_ROWS = 6;

export type HomeProps = {
  slug: string;
  // Bump to read everything again: the shell does on every change (#137).
  version?: number;
  // The shell's actions; without them Home navigates to the page that has them.
  onAsk?: () => void;
  onBlock?: (date: string) => void;
  onOpenBooking?: (id: string, date: string) => void;
  onOpenDay?: (date: string) => void;
};

// `samples`: the merchant isn't published yet, so `day` holds the web's samples (#136).
type HomeData = {
  merchant: MerchantOverview;
  dashboard: Dashboard;
  day: ReservationDay;
  samples: boolean;
};

/** Ids that appeared since the previous read: their rows get the `.fresh` highlight. */
// Computed while rendering (React's "adjust state on a prop change"), so a new row is
// highlighted on the same render that shows it, never one frame later.
type FreshState = { key: string | null; seen: Set<string> | null; fresh: Set<string> };

function useFresh(ids: readonly string[] | null): Set<string> {
  const key = ids?.join("\n") ?? null;
  const [state, setState] = useState<FreshState>({ key: null, seen: null, fresh: new Set() });
  if (key === null || key === state.key) return state.fresh;
  const current = key === "" ? [] : key.split("\n");
  const before = state.seen;
  const next: FreshState = {
    key,
    seen: new Set(current),
    fresh: before ? new Set(current.filter((id) => !before.has(id))) : new Set(),
  };
  setState(next);
  return next.fresh;
}

function useHomeData(slug: string, client: ManagementClient, version: number) {
  const [data, setData] = useState<HomeData | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The version Home last showed: a read for a newer one crossfades in (#137).
  const shown = useRef<number | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    const { signal } = abort;
    (async () => {
      const [merchant, dashboard] = await Promise.all([
        client.merchant(slug, signal),
        client.dashboard(slug, signal),
      ]);
      const day = await client.reservations(slug, dashboard.date, signal);
      if (signal.aborted) return;
      const samples = merchant.status === "building";
      const reservations = samples
        ? sampleBookings(dashboard.date, dashboard.room.service)
        : day.reservations;
      const live = shown.current !== null && shown.current !== version;
      shown.current = version;
      const apply = () => {
        setData({ merchant, dashboard, day: { ...day, reservations }, samples });
        setError(null);
      };
      if (live) crossfade(apply);
      else apply();
    })().catch((reason: unknown) => {
      if (signal.aborted) return;
      setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => abort.abort();
  }, [slug, client, version]);
  return { data, error };
}

export function HomePage({
  slug,
  version = 0,
  onAsk,
  onBlock,
  onOpenBooking,
  onOpenDay,
}: HomeProps) {
  const navigate = useNavigate();
  const client = useClient();
  const base = `/manage/${encodeURIComponent(slug)}`;
  const { data, error } = useHomeData(slug, client, version);
  const clock = useNow();

  const ask = onAsk ?? (() => navigate(`${base}/assistant`));
  const block = onBlock ?? ((date: string) => navigate(`${base}/reservations?date=${date}&block=1`));
  const openBooking =
    onOpenBooking ?? ((id: string, date: string) => navigate(`${base}/reservations?date=${date}&booking=${encodeURIComponent(id)}`));
  const openDay = onOpenDay ?? ((date: string) => navigate(`${base}/reservations?date=${date}`));

  const tonight = data ? tonightOf(data) : [];
  const freshBookings = useFresh(data ? tonight.map((b) => b.id) : null);
  const freshActivity = useFresh(data ? data.dashboard.activity.map((a) => String(a.id)) : null);

  const name = firstName(data?.merchant.owner_name ?? null);
  const hello = greeting(hourIn(clock, data?.merchant.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone));

  return (
    <div className="home">
      <div className="ttl">
        <h1>
          {hello}
          {name && (
            <>
              , <Under>{name}</Under>
            </>
          )}
        </h1>
        {data && <p>{subtitle(data.dashboard.date, data.dashboard.room.service)}</p>}
        <div className="pills">
          <Pill primary onClick={ask}>
            Ask the assistant
          </Pill>
          <Pill disabled={!data} onClick={() => data && block(data.dashboard.date)}>
            Block a time
          </Pill>
          <Link className="pill" to={`${base}/reservations`}>
            Reservations
          </Link>
          <Link className="pill" to={`${base}/hours`}>
            Opening hours
          </Link>
          <Link className="pill" to={`${base}/floor`}>
            Floor plan
          </Link>
        </div>
      </div>
      {error && !data && (
        <p className="none" role="alert">
          {error}
        </p>
      )}
      {data && (
        <div className="dash">
          <GuestsCard history={data.dashboard.history} />
          <TonightCard
            bookings={tonight}
            fresh={freshBookings}
            date={data.dashboard.date}
            base={base}
            onOpen={openBooking}
            samples={data.samples}
          />
          <NextDaysCard days={data.dashboard.next_days} onOpen={openDay} />
          <SourcesCard dashboard={data.dashboard} />
          <RoomCard dashboard={data.dashboard} base={base} />
          <ActivityCard items={data.dashboard.activity} fresh={freshActivity} />
        </div>
      )}
    </div>
  );
}

/** Today's bookings in the room's service, live and cancelled, by time. */
function tonightOf({ dashboard, day }: HomeData): ManagedReservation[] {
  const { service } = dashboard.room;
  const start = minutes(service.start);
  const end = minutes(service.end);
  return day.reservations
    .filter((b) => b.date === dashboard.date && minutes(b.time) >= start && minutes(b.time) < end)
    .sort((a, b) => a.time.localeCompare(b.time) || a.id.localeCompare(b.id));
}

function TonightCard({
  bookings: list,
  fresh,
  date,
  base,
  onOpen,
  samples,
}: {
  bookings: ManagedReservation[];
  fresh: Set<string>;
  date: string;
  base: string;
  onOpen: (id: string, date: string) => void;
  samples: boolean;
}) {
  const live = list.filter((b) => b.status === "confirmed");
  const seated = live.reduce((sum, b) => sum + b.party_size, 0);
  return (
    <section className="m s5" aria-labelledby="home-tonight">
      <div className="m-hd">
        <h2 id="home-tonight">{samples ? "Tonight, samples" : "Tonight"}</h2>
        <span className="num">
          {guests(seated)}, {bookings(live.length)}
        </span>
      </div>
      {samples && (
        <p className="none" role="note">
          {SAMPLES_NOTE}
        </p>
      )}
      <div className="t-rows">
        {list.length === 0 ? (
          <p className="none">{NOTHING_TONIGHT}</p>
        ) : (
          list.slice(0, TONIGHT_ROWS).map((b) => (
            <button
              key={b.id}
              type="button"
              className={["t-row", b.status === "confirmed" ? "" : "off", fresh.has(b.id) ? "fresh" : ""]
                .filter(Boolean)
                .join(" ")}
              // A sample booking exists on no server: nothing to open.
              disabled={samples}
              onClick={() => onOpen(b.id, b.date)}
            >
              <span className="t-time">{b.time}</span>
              <span className="t-name">{b.guest_name}</span>
              <span className="t-det">
                {b.party_size}, {b.table_label}
              </span>
              <Source via={b.via} />
            </button>
          ))
        )}
      </div>
      {list.length > 0 && !samples && (
        <div>
          <Link className="link" to={`${base}/reservations?date=${date}`}>
            See all {bookings(live.length)}
          </Link>
        </div>
      )}
    </section>
  );
}

function NextDaysCard({
  days,
  onOpen,
}: {
  days: Dashboard["next_days"];
  onOpen: (date: string) => void;
}) {
  const max = Math.max(1, ...days.map((d) => d.guests));
  const total = days.reduce((sum, d) => sum + d.guests, 0);
  return (
    <Box tone="brick" className="m s4" aria-labelledby="home-week">
      <div className="m-hd">
        <h2 id="home-week">Next 7 days</h2>
      </div>
      <div className="big">
        <b className="num">{count(total)}</b>
        <span>guests booked</span>
      </div>
      <div className="bars">
        {days.map((d, index) => (
          <button
            key={d.date}
            type="button"
            className={index === 0 ? "bar today" : "bar"}
            aria-label={`${index === 0 ? "Today, " : ""}${dayShort(d.date)}, ${guests(d.guests)}`}
            title={guests(d.guests)}
            onClick={() => onOpen(d.date)}
          >
            <span>
              <i style={{ height: `${(d.guests / max) * 100}%` }} />
            </span>
            <small>{weekday(d.date)}</small>
          </button>
        ))}
      </div>
    </Box>
  );
}

function SourcesCard({ dashboard }: { dashboard: Dashboard }) {
  const { share, total } = agentShare(dashboard.channels);
  const you = total === 0 ? 0 : 100 - share;
  return (
    <Box tone="cream" className="m s4" aria-labelledby="home-sources">
      <div className="m-hd">
        <h2 id="home-sources">Where bookings come from</h2>
      </div>
      <div className="big">
        <b className="num">{share}%</b>
        <span>through your agent</span>
      </div>
      <div className={total === 0 ? "split empty" : "split"} role="img" aria-label={`${share}% agent, ${you}% you`}>
        {total > 0 && (
          <>
            <i style={{ width: `${share}%` }} />
            <i style={{ width: `${you}%` }} />
          </>
        )}
      </div>
      <div className="leg num">
        <span>
          <i className="a" />
          Agent {share}%
        </span>
        <span>
          <i />
          You {you}%
        </span>
      </div>
      <small className="num home-month">{bookings(total)} in the last 30 days</small>
    </Box>
  );
}

function RoomCard({ dashboard, base }: { dashboard: Dashboard; base: string }) {
  const { room } = dashboard;
  return (
    <section className="m s4" aria-labelledby="home-room">
      <div className="m-hd">
        <h2 id="home-room">The room tonight</h2>
      </div>
      <div className="big">
        <b className="num">{room.guests}</b>
        <span>of {room.seats} seats booked</span>
      </div>
      <Link className="minis" to={`${base}/floor`} aria-label="See the floor plan">
        {room.tables.map((table, index) => (
          <span
            key={table.label}
            className={["mini", table.seats >= 4 ? "w" : "", table.booked ? "on" : ""].filter(Boolean).join(" ")}
          >
            <Blob index={index} />
            <span>{table.label}</span>
          </span>
        ))}
      </Link>
      <div className="pills">
        <Tag className="num">
          {room.next_free
            ? `Next table for ${room.party_size}, ${room.next_free}`
            : `No table for ${room.party_size} left`}
        </Tag>
        <Tag className="num">Groups up to {room.largest_group}</Tag>
      </div>
    </section>
  );
}

function ActivityCard({ items, fresh }: { items: ActivityItem[]; fresh: Set<string> }) {
  const latest = items.slice(0, ACTIVITY_ROWS);
  return (
    <section className="m s12" aria-labelledby="home-activity">
      <div className="m-hd">
        <h2 id="home-activity">Recent activity</h2>
      </div>
      <div>
        {latest.length === 0 ? (
          <p className="none">{NO_ACTIVITY}</p>
        ) : (
          latest.map((item) => (
            <div key={item.id} className={fresh.has(String(item.id)) ? "act fresh" : "act"}>
              <time dateTime={item.at}>{clockOf(item.at)}</time>
              <span>{item.text}</span>
              <em>{KIND[item.kind]}</em>
              <Source via={item.source} />
            </div>
          ))
        )}
      </div>
    </section>
  );
}
