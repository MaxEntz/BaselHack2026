import { type CSSProperties, useCallback, useState } from "react";
import { plural } from "../../cards/format.ts";
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
import { BookingDialog } from "./reservations/BookingDialog.tsx";
import { NewBookingDialog } from "./reservations/NewBookingDialog.tsx";
import { hasEnded, hasStarted } from "./reservations/words.ts";

const DAYS_AHEAD = 7;
// Vertical offsets of the tables, by index, so that the room doesn't look like a grid.
const OFFSETS = [0, 16, 4, 20, 2, 14, 6, 18, 0];
const SOON_MIN = 60;

const surname = (name: string) => name.split(" ").at(-1);

type FloorBase = { today: string; clock: string; tables: TableSpec[] };

export function FloorPage({ slug, version }: { slug: string; version: number }) {
  const client = useClient();
  // The merchant's today and time are the platform's, never the browser's. Read once: while the
  // page stays open, the default time and what has passed don't follow the clock.
  const loadNow = useCallback(async (signal: AbortSignal) => (await client.merchant(slug, signal)).now, [client, slug]);
  const loadTables = useCallback(
    async (signal: AbortSignal) => (await client.tables(slug, signal)).tables,
    [client, slug],
  );
  const now = useLoad(loadNow);
  const tables = useLoad(loadTables, version);
  const error = now.error ?? tables.error;

  return (
    <>
      <PageTitle title={PAGES.floor} />
      {error && (
        <p className="err" role="alert">
          {error}
        </p>
      )}
      {now.data && tables.data && (
        <>
          <Room
            slug={slug}
            version={version}
            today={now.data.slice(0, 10)}
            clock={now.data.slice(11, 16)}
            tables={tables.data}
          />
          <TablesForm slug={slug} tables={tables.data} onSaved={(saved) => tables.update(() => saved)} />
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
  // The open dialog: a booking's, or a new booking for that many guests at the time shown.
  const [opened, setOpened] = useState<string | null>(null);
  const [party, setParty] = useState<number | null>(null);
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
  const booking = data.reservations.find((b) => b.id === opened) ?? null;

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
          day={data}
          tables={tables}
          at={at}
          starts={starts}
          passed={isToday && at <= clock}
          onAt={(next) => setChosen({ service, at: next })}
          onOpen={setOpened}
          onNew={setParty}
        />
      </section>
      <BookingDialog
        slug={slug}
        booking={booking}
        slotLengthMin={data.slot_length_min}
        started={booking !== null && hasStarted(booking, today, clock)}
        ended={booking !== null && hasEnded(booking, today, clock, data.slot_length_min)}
        onClose={() => setOpened(null)}
        onMoved={() => setOpened(null)}
        onCancelled={() => setOpened(null)}
      />
      <NewBookingDialog
        slug={slug}
        day={data.date}
        limit={data.largest_group}
        service={data.services[service]}
        preset={{ time: at, party: party ?? undefined }}
        open={party !== null}
        onClose={() => setParty(null)}
        onAdded={() => setParty(null)}
      />
    </>
  );
}

type RoomAtProps = {
  day: ReservationDay;
  tables: TableSpec[];
  at: string;
  starts: string[];
  // `at` has passed: no new booking there.
  passed: boolean;
  onAt: (at: string) => void;
  onOpen: (id: string) => void;
  // A new booking at `at`, for that many guests.
  onNew: (party: number) => void;
};

function RoomAt({ day, tables, at, starts, passed, onAt, onOpen, onNew }: RoomAtProps) {
  const toast = useToast();
  const { seated, soon } = occupancy(day, at);
  const guests = [...seated.values()].reduce((sum, booking) => sum + booking.party_size, 0);
  const blocked = day.blocked.includes(at);

  // A new booking doesn't take the table clicked: the table choice stays automatic (AGENTS.md §6).
  function choose(table: TableSpec) {
    const booking = seated.get(table.label);
    if (booking) onOpen(booking.id);
    else if (passed) toast(`${at} has passed.`);
    else if (blocked) toast(`${at} is blocked.`);
    else onNew(Math.min(2, table.seats));
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
        {tables.map((table, index) => (
          <TableButton
            key={table.label}
            table={table}
            index={index}
            booking={seated.get(table.label)}
            soon={soon.has(table.label)}
            onChoose={choose}
          />
        ))}
      </div>
      <Legend />
    </>
  );
}

type Occupancy = {
  // The booking seated at each table, by label.
  seated: Map<string, ManagedReservation>;
  // The tables a booking arrives at within the hour.
  soon: Set<string>;
};

// Who sits where at `at`, and which tables fill up within the hour.
function occupancy(day: ReservationDay, at: string): Occupancy {
  const now = toMinutes(at);
  const seated = new Map<string, ManagedReservation>();
  const soon = new Set<string>();
  for (const booking of day.reservations) {
    if (booking.status !== "confirmed") continue;
    const start = toMinutes(booking.time);
    if (start <= now && now < start + day.slot_length_min) seated.set(booking.table_label, booking);
    else if (start > now && start - now <= SOON_MIN) soon.add(booking.table_label);
  }
  return { seated, soon };
}

type TableButtonProps = {
  table: TableSpec;
  index: number;
  // The booking seated there, if any.
  booking: ManagedReservation | undefined;
  soon: boolean;
  onChoose: (table: TableSpec) => void;
};

function TableButton({ table, index, booking, soon, onChoose }: TableButtonProps) {
  const state = booking ? "busy" : soon ? "soon" : "";
  return (
    <button
      type="button"
      className={["tb", table.seats >= 4 && "s4", state].filter(Boolean).join(" ")}
      style={{ "--y": `${OFFSETS[index % OFFSETS.length]}px` } as CSSProperties}
      aria-label={
        booking
          ? `Table ${table.label}, ${surname(booking.guest_name)}, ${plural(booking.party_size, "guest")}`
          : `Table ${table.label}, ${table.seats} seats, free`
      }
      onClick={() => onChoose(table)}
    >
      <Blob index={index} />
      {/* The name holds the words shown, in order, for voice control (WCAG 2.5.3, #153). */}
      <span>
        <b>{table.label}</b>{" "}
        <small>
          {booking ? `${surname(booking.guest_name)}, ${booking.party_size}` : `${table.seats} seats`}
        </small>
      </span>
    </button>
  );
}

function Legend() {
  return (
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
