import { type CSSProperties, useCallback, useState } from "react";
import { useNavigate } from "react-router";
import type { ManagedReservation, ReservationDay, Service, TableSpec } from "../../types.ts";
import { DayBar } from "../DayBar.tsx";
import { useClient, useLoad } from "../data.ts";
import { addDays } from "../dates.ts";
import { LeaveGuard } from "../LeaveGuard.tsx";
import { PAGES } from "../pages.ts";
import { defaultService, toMinutes } from "../services.ts";
import { Blob, Icon, Pill, SaveBar, useToast } from "../ui/index.ts";
import { useSavedForm } from "../useSavedForm.tsx";
import { PageTitle } from "./PageTitle.tsx";

const DAYS_AHEAD = 7;
// Vertical offsets of the tables, by index, so that the room doesn't look like a grid.
const OFFSETS = [0, 16, 4, 20, 2, 14, 6, 18, 0];
const SOON_MIN = 60;

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

type FloorBase = { today: string; clock: string; tables: TableSpec[] };

export function FloorPage({ slug, version }: { slug: string; version: number }) {
  const client = useClient();
  const load = useCallback(
    async (signal: AbortSignal): Promise<FloorBase> => {
      const [merchant, { tables }] = await Promise.all([
        client.merchant(slug, signal),
        client.tables(slug, signal),
      ]);
      // The merchant's today and time are the platform's, never the browser's. Read once: while the
      // page stays open, the default time and what has passed don't follow the clock.
      return { today: merchant.now.slice(0, 10), clock: merchant.now.slice(11, 16), tables };
    },
    [client, slug],
  );
  const { data, error, update } = useLoad(load);

  return (
    <>
      <PageTitle title={PAGES.floor} />
      {error && (
        <p className="err" role="alert">
          {error}
        </p>
      )}
      {data && (
        <>
          <Room slug={slug} version={version} {...data} />
          <TablesForm
            slug={slug}
            tables={data.tables}
            onSaved={(tables) => update((loaded) => ({ ...loaded, tables }))}
          />
        </>
      )}
    </>
  );
}

/** Today during the service: its latest start so far; otherwise its middle start. */
function defaultTime(starts: readonly string[], service: Service, today: boolean, clock: string): string {
  if (today && service.start <= clock && clock < service.end) {
    return starts.findLast((start) => start <= clock) ?? starts[0];
  }
  return starts[Math.floor(starts.length / 2)];
}

function Room({ slug, version, today, clock, tables }: FloorBase & { slug: string; version: number }) {
  const client = useClient();
  const [day, setDay] = useState(today);
  const [chosen, setChosen] = useState<{ service?: number; at?: string }>({});
  const load = useCallback(
    (signal: AbortSignal) => client.reservations(slug, day, signal),
    [client, slug, day],
  );
  // Only the room follows live events: the form below keeps its draft (#137).
  const { data, error } = useLoad(load, version);
  const days = Array.from({ length: DAYS_AHEAD }, (_, i) => addDays(today, i));

  if (error) {
    return (
      <p className="err" role="alert">
        {error}
      </p>
    );
  }
  // A day without services has no room to show. While another day loads, the last one stays.
  if (!data || data.services.length === 0) return null;

  const isToday = data.date === today;
  const service = chosen.service ?? defaultService(data.services, isToday, clock);
  const { start, end } = data.services[service];
  const starts = data.starts.filter((time) => start <= time && time < end);
  const at =
    chosen.at && starts.includes(chosen.at)
      ? chosen.at
      : defaultTime(starts, data.services[service], isToday, clock);

  return (
    <>
      <DayBar
        days={days}
        day={day}
        onDay={(next) => {
          setDay(next);
          setChosen({});
        }}
        services={data.services}
        service={service}
        onService={(index) => setChosen({ service: index })}
      />
      <section className="room on-dark">
        <RoomAt
          slug={slug}
          day={data}
          tables={tables}
          at={at}
          starts={starts}
          passed={isToday && at <= clock}
          onAt={(next) => setChosen({ service, at: next })}
        />
      </section>
    </>
  );
}

function RoomAt({
  slug,
  day,
  tables,
  at,
  starts,
  passed,
  onAt,
}: {
  slug: string;
  day: ReservationDay;
  tables: TableSpec[];
  at: string;
  starts: string[];
  // `at` has passed: no new booking there.
  passed: boolean;
  onAt: (at: string) => void;
}) {
  const navigate = useNavigate();
  const toast = useToast();
  const now = toMinutes(at);
  const seated = new Map<string, ManagedReservation>();
  const soon = new Set<string>();
  for (const booking of day.reservations) {
    if (booking.status !== "confirmed") continue;
    const start = toMinutes(booking.time);
    if (start <= now && now < start + day.slot_length_min) seated.set(booking.table_label, booking);
    else if (start > now && start - now <= SOON_MIN) soon.add(booking.table_label);
  }
  const guests = [...seated.values()].reduce((sum, booking) => sum + booking.party_size, 0);
  const blocked = day.blocked.includes(at);
  const base = `/manage/${encodeURIComponent(slug)}/reservations?date=${day.date}`;

  // The booking dialogs are the Reservations page's (#139): open them there.
  function choose(table: TableSpec) {
    const booking = seated.get(table.label);
    if (booking) navigate(`${base}&booking=${encodeURIComponent(booking.id)}`);
    else if (passed) toast(`${at} has passed.`);
    else if (blocked) toast(`${at} is blocked.`);
    else navigate(`${base}&new=${at}&guests=${Math.min(2, table.seats)}`);
  }

  return (
    <>
      <div className="m-hd">
        <h2 className="dsp">The room at {at}</h2>
        <span className="num">
          {seated.size} of {plural(tables.length, "table")}, {plural(guests, "guest")} seated
        </span>
      </div>
      <div className="pills" role="group" aria-label="Time">
        {starts.map((start) => (
          <Pill
            small
            key={start}
            className={day.blocked.includes(start) ? "num off" : "num"}
            aria-pressed={start === at}
            onClick={() => onAt(start)}
          >
            {start}
          </Pill>
        ))}
      </div>
      <div className="floor">
        {tables.map((table, index) => {
          const booking = seated.get(table.label);
          const state = booking ? "busy" : soon.has(table.label) ? "soon" : "";
          return (
            <button
              key={table.label}
              type="button"
              className={["tb", table.seats >= 4 && "s4", state].filter(Boolean).join(" ")}
              style={{ "--y": `${OFFSETS[index % OFFSETS.length]}px` } as CSSProperties}
              aria-label={
                booking
                  ? `Table ${table.label}, ${booking.guest_name}, ${plural(booking.party_size, "guest")}`
                  : `Table ${table.label}, ${table.seats} seats, free`
              }
              onClick={() => choose(table)}
            >
              <Blob index={index} />
              <span>
                <b>{table.label}</b>
                <small>
                  {booking
                    ? `${booking.guest_name.split(" ").at(-1)}, ${booking.party_size}`
                    : `${table.seats} seats`}
                </small>
              </span>
            </button>
          );
        })}
      </div>
      <div className="lg">
        <span>
          <i className="f" />
          Seated
        </span>
        <span>
          <i className="d" />
          Arriving within the hour
        </span>
        <span>
          <i />
          Free
        </span>
      </div>
    </>
  );
}

function TablesForm({
  slug,
  tables,
  onSaved,
}: {
  slug: string;
  tables: TableSpec[];
  onSaved: (tables: TableSpec[]) => void;
}) {
  const client = useClient();
  const form = useSavedForm(tables, async (draft) => {
    const saved = await client.saveTables(slug, { tables: draft });
    onSaved(saved.tables);
    return saved.tables;
  });
  const seats = form.value.reduce((sum, table) => sum + table.seats, 0);

  function add() {
    const numbers = form.value.map((table) => Number(/^T(\d+)$/.exec(table.label)?.[1] ?? 0));
    form.set([...form.value, { label: `T${Math.max(0, ...numbers) + 1}`, seats: 4 }]);
  }

  return (
    <form className="forms" noValidate onSubmit={form.submit}>
      <div className="fs">
        <h3 className="num">
          {plural(form.value.length, "table")}, {plural(seats, "seat")}
        </h3>
        <div className="tbs">
          {form.value.map((table, index) => (
            <div className="tbr" key={table.label}>
              <b>{table.label}</b>
              <div>
                <label className="sr" htmlFor={`tb-${index}`}>
                  Seats at {table.label}
                </label>
                <input
                  id={`tb-${index}`}
                  type="number"
                  min={1}
                  max={30}
                  // Empty while typing; an empty table has no seats, which the server refuses.
                  value={table.seats || ""}
                  onChange={(event) =>
                    form.set(
                      form.value.map((other, i) =>
                        i === index ? { ...other, seats: Number(event.target.value) } : other,
                      ),
                    )
                  }
                />
              </div>
              <button
                type="button"
                aria-label={`Remove ${table.label}`}
                onClick={() => form.set(form.value.filter((_, i) => i !== index))}
              >
                <Icon name="x" />
              </button>
            </div>
          ))}
        </div>
        <div>
          <Pill small onClick={add}>
            Add a table
          </Pill>
        </div>
      </div>
      <SaveBar dirty={form.dirty} error={form.error} onDiscard={form.discard} />
      <LeaveGuard dirty={form.dirty} />
    </form>
  );
}
