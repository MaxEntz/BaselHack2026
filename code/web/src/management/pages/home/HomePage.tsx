import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { now, useNow } from "../../../clock.ts";
import { isMoment, shownMoment } from "../../../moments.ts";
import type {
  ActivityItem,
  Dashboard,
  ManagedReservation,
  MerchantOverview,
  ReservationDay,
  Rules,
} from "../../../types.ts";
import type { ManagementClient } from "../../client.ts";
import { useClient } from "../../data.ts";
import { useFresh } from "../../fresh.ts";
import { dayShort, weekday } from "../../dates.ts";
import { crossfade } from "../../motion.ts";
import { bookingPath, dayPath, pagePath } from "../../pages.ts";
import { sampleBookings } from "../../samples.ts";
import { Blob, Box, Pill, Source, Tag, Under } from "../../ui/index.ts";
import { GuestsCard } from "./GuestsCard.tsx";
import { MomentPanel } from "./MomentPanel.tsx";
import {
  KIND,
  agentShare,
  bookings,
  clockOf,
  count,
  firstName,
  greeting,
  guests,
  hourIn,
  minutes,
  subtitle,
  timeIn,
} from "./format.ts";
import "../../pages.css";
import "./home.css";

export const NOTHING_TONIGHT = "Nothing booked for tonight yet.";
export const NO_ACTIVITY = "Nothing has happened yet.";
const SAMPLES_NOTE = "Sample bookings: your agent is hidden from guests until you publish it.";
const TONIGHT_ROWS = 9;
const ACTIVITY_ROWS = 6;

export type HomeProps = {
  slug: string;
  // Bump to read everything again: the shell does on every change (#137).
  version?: number;
  // The shell opens its assistant column; without it, Home goes to the Assistant page.
  onAsk?: () => void;
};

// `samples`: the merchant isn't published yet, so `day` holds the web's samples (#136).
type HomeData = {
  merchant: MerchantOverview;
  dashboard: Dashboard;
  day: ReservationDay;
  rules: Rules;
  samples: boolean;
  // The platform's clock against this browser's: the moment of the day is the platform's (#137).
  skew: number;
};

function useHomeData(slug: string, client: ManagementClient, version: number) {
  const [data, setData] = useState<HomeData | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The version Home last showed: a read for a newer one crossfades in (#137).
  const shown = useRef<number | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    const { signal } = abort;
    (async () => {
      const [merchant, dashboard, rules] = await Promise.all([
        client.merchant(slug, signal),
        client.dashboard(slug, signal),
        client.rules(slug, signal),
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
        const skew = Date.parse(merchant.now) - now().getTime();
        setData({ merchant, dashboard, day: { ...day, reservations }, rules, samples, skew });
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

export function HomePage({ slug, version = 0, onAsk }: HomeProps) {
  const navigate = useNavigate();
  const client = useClient();
  const { data, error } = useHomeData(slug, client, version);
  const clock = useNow();

  const ask = onAsk ?? (() => navigate(pagePath(slug, "assistant")));
  const block = (date: string) => navigate(dayPath(slug, date, { block: true }));
  const openBooking = (id: string, date: string) => navigate(bookingPath(slug, date, id));
  const openDay = (date: string) => navigate(dayPath(slug, date));

  // Asked for from Studio's dock, or the clock's (#154).
  const [params] = useSearchParams();
  const asked = params.get("moment");
  const previewed = isMoment(asked) ? asked : null;
  const moment = data
    ? shownMoment(previewed, timeIn(new Date(clock.getTime() + data.skew), data.merchant.timezone), data.day.services)
    : null;

  const tonight = data ? tonightOf(data) : [];
  const freshBookings = useFresh(data ? tonight.map((b) => b.id) : null);
  const freshActivity = useFresh(data ? data.dashboard.activity.map((a) => String(a.id)) : null);

  const name = firstName(data?.merchant.owner_name ?? null);
  const hour = previewed && moment
    ? Number(moment.time.slice(0, 2))
    : hourIn(clock, data?.merchant.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone);
  const hello = greeting(hour);

  return (
    <div className="home">
      <HomeTitle
        slug={slug}
        hello={hello}
        name={name}
        line={data ? subtitle(data.dashboard.date, data.dashboard.room.service) : null}
        onAsk={ask}
        onBlock={data ? () => block(data.dashboard.date) : null}
      />
      {error && !data && (
        <p className="none" role="alert">
          {error}
        </p>
      )}
      {data && moment && (
        <MomentPanel
          at={moment}
          previewed={previewed !== null}
          samples={data.samples}
          allServices={data.day.services}
          reservations={data.day.reservations.filter((booking) => booking.date === data.dashboard.date)}
          blocked={data.day.blocked}
          slotLength={data.day.slot_length_min}
          seatCount={data.dashboard.room.seats}
          largestTable={Math.max(0, ...data.dashboard.room.tables.map((table) => table.seats))}
          rules={data.rules}
        />
      )}
      {data && (
        <div className="dash">
          <GuestsCard history={data.dashboard.history} />
          <TonightCard
            bookings={tonight}
            fresh={freshBookings}
            day={dayPath(slug, data.dashboard.date)}
            onOpen={openBooking}
            samples={data.samples}
          />
          <NextDaysCard days={data.dashboard.next_days} onOpen={openDay} />
          <SourcesCard dashboard={data.dashboard} />
          <RoomCard dashboard={data.dashboard} floor={pagePath(slug, "floor")} />
          <ActivityCard items={data.dashboard.activity} fresh={freshActivity} />
        </div>
      )}
    </div>
  );
}

type HomeTitleProps = {
  slug: string;
  hello: string;
  name: string | null;
  // The day and its service, once the dashboard is there.
  line: string | null;
  onAsk: () => void;
  // Null until the dashboard is there.
  onBlock: (() => void) | null;
};

// The greeting, the day, and the shortcuts to what the merchant does most.
function HomeTitle({ slug, hello, name, line, onAsk, onBlock }: HomeTitleProps) {
  return (
    <div className="ttl">
      <h1>
        {hello}
        {name && (
          <>
            , <Under>{name}</Under>
          </>
        )}
      </h1>
      {line !== null && <p>{line}</p>}
      <div className="pills">
        <Pill primary onClick={onAsk}>
          Ask the assistant
        </Pill>
        <Pill disabled={!onBlock} onClick={() => onBlock?.()}>
          Block a time
        </Pill>
        <Link className="pill" to={pagePath(slug, "reservations")}>
          Reservations
        </Link>
        <Link className="pill" to={pagePath(slug, "hours")}>
          Opening hours
        </Link>
        <Link className="pill" to={pagePath(slug, "floor")}>
          Floor plan
        </Link>
      </div>
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
  day,
  onOpen,
  samples,
}: {
  bookings: ManagedReservation[];
  fresh: Set<string>;
  // Today on the Reservations page.
  day: string;
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
          <Link className="link" to={day}>
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

function RoomCard({ dashboard, floor }: { dashboard: Dashboard; floor: string }) {
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
      {/* A picture of the room, named by where it goes: its labels are part of the picture. */}
      <Link className="minis" to={floor}>
        <span className="sr">See the floor plan</span>
        {room.tables.map((table, index) => (
          <span
            key={table.label}
            className={["mini", table.seats >= 4 ? "w" : "", table.booked ? "on" : ""].filter(Boolean).join(" ")}
            aria-hidden="true"
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
