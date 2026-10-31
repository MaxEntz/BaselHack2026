import {
  ManagementRequestError,
  type ManagementClient,
} from "../management/client.ts";
import type {
  ActivityItem,
  ActivityKind,
  DaySlot,
  ErrorCode,
  ManagedReservation,
  ManagementFixture,
  Problem,
  Room,
  Service,
  TableSpec,
  Via,
} from "../types.ts";

const LATENCY_MS = 120;
const LIVE_BOOKING_DELAY_MS = 6000;
// AGENTS.md §6: bookable every 30 minutes, last seating an hour before the end.
const STEP_MIN = 30;
const LAST_SEATING_MIN = 60;
const DAYS_AHEAD = 7;
const ROOM_PARTY = 4;
const SEARCH_LIMIT = 8;
const REQUESTS_LIMIT = 10;
const ACTIVITY_LIMIT = 6;
const NOT_FOUND = "No booking with that reference.";
const TAKEN = "That time is no longer free.";

// The management site against the demo fixture, with no platform (VITE_MOCK=1).
// It applies the server's rules (#133, #134), not the mockup's: past times are
// refused. The clock starts at `fixture.now` and runs; local times keep the
// offset of `fixture.now`, as the demo week has no change of clock.
export function createMockManagementClient(
  fixture: ManagementFixture,
  { latencyMs = LATENCY_MS, liveBookingDelayMs = LIVE_BOOKING_DELAY_MS } = {},
): ManagementClient {
  const started = Date.now();
  const offset = fixture.now.slice(-6);
  const today = fixture.now.slice(0, 10);
  let data = structuredClone(fixture);
  let lastActivity = Math.max(0, ...data.activity.map((item) => item.id));
  const keys = new Map<string, string>();
  const baseline = liveByVia(today);
  let notified = false;

  const now = () => new Date(Date.parse(fixture.now) + Date.now() - started);
  const instant = (date: string, time: string) => `${date}T${time}:00${offset}`;
  const nowIso = () => {
    const { date, time } = local(now(), data.merchant.timezone);
    return instant(date, time);
  };
  const isPast = (startsAt: string) => Date.parse(startsAt) <= now().getTime();

  function respond<T>(value: T): Promise<T> {
    const copy = structuredClone(value);
    return new Promise((resolve) => setTimeout(() => resolve(copy), latencyMs));
  }

  function fail(
    status: number,
    error: ErrorCode,
    message: string,
    problem?: Problem,
  ): never {
    const fallback =
      error === "party_too_large" ? { fallback_url: data.rules.fallback_url } : {};
    throw new ManagementRequestError(status, { error, message, ...fallback, problem });
  }

  function invalid(code: Problem["code"], message: string, extra: Partial<Problem> = {}): never {
    return fail(422, "invalid_input", message, { code, ...extra });
  }

  function log(kind: ActivityKind, text: string, source: Via, reservation: string | null = null) {
    lastActivity += 1;
    const item: ActivityItem = {
      id: lastActivity,
      at: nowIso(),
      kind,
      text,
      source,
      reservation_id: reservation,
    };
    data = { ...data, activity: [item, ...data.activity] };
  }

  function liveByVia(date: string): Map<Via, number> {
    const counts = new Map<Via, number>();
    for (const booking of data.reservations) {
      if (booking.date === date && booking.status === "confirmed") {
        counts.set(booking.via, (counts.get(booking.via) ?? 0) + 1);
      }
    }
    return counts;
  }

  function largestGroup(): number {
    return Math.min(data.rules.max_party_size, Math.max(...data.tables.map((table) => table.seats)));
  }

  function slotsOf(date: string, partySize: number, ignore?: string): DaySlot[] {
    const length = data.rules.slot_length_min;
    const blocked = new Set(data.blocked.filter((b) => b.date === date).map((b) => b.time));
    const live = data.reservations.filter(
      (b) => b.date === date && b.status === "confirmed" && b.id !== ignore,
    );
    // Smallest fitting table first (AGENTS.md §6), in floor-plan order among equals.
    const fitting =
      partySize > data.rules.max_party_size
        ? []
        : data.tables
            .filter((table) => table.seats >= partySize)
            .toSorted((a, b) => a.seats - b.seats);
    const slots: DaySlot[] = [];
    for (const service of data.hours.services) {
      const last = minutes(service.end) - LAST_SEATING_MIN;
      for (let start = minutes(service.start); start <= last; start += STEP_MIN) {
        const time = hhmm(start);
        const startsAt = instant(date, time);
        if (isPast(startsAt)) continue;
        const table = fitting.find(
          (candidate) =>
            !live.some(
              (b) =>
                b.table_label === candidate.label &&
                minutes(b.time) < start + length &&
                start < minutes(b.time) + length,
            ),
        );
        slots.push({
          starts_at: startsAt,
          time,
          until: hhmm(start + length),
          table: table ?? null,
          blocked: blocked.has(time),
        });
      }
    }
    return slots;
  }

  function room(): Room {
    const clock = local(now(), data.merchant.timezone);
    const services = data.hours.services.toSorted((a, b) => a.start.localeCompare(b.start));
    const service =
      services.find((s) => minutes(s.end) > minutes(clock.time)) ?? services.at(-1) ?? null;
    const within = (time: string, s: Service) =>
      minutes(time) >= minutes(s.start) && minutes(time) < minutes(s.end);
    const live = service
      ? data.reservations.filter(
          (b) => b.date === clock.date && b.status === "confirmed" && within(b.time, service),
        )
      : [];
    const booked = new Set(live.map((b) => b.table_label));
    const largest = largestGroup();
    const partySize = Math.min(ROOM_PARTY, largest);
    const next = service
      ? slotsOf(clock.date, partySize).find((s) => !s.blocked && s.table && within(s.time, service))
      : undefined;
    return {
      service,
      seats: data.tables.reduce((sum, table) => sum + table.seats, 0),
      guests: live.reduce((sum, b) => sum + b.party_size, 0),
      tables: data.tables.map((table) => ({ ...table, booked: booked.has(table.label) })),
      party_size: partySize,
      next_free: next?.time ?? null,
      largest_group: largest,
    };
  }

  function find(id: string): ManagedReservation {
    const booking = data.reservations.find((b) => b.id === id.trim().toUpperCase());
    return booking ?? fail(404, "not_found", NOT_FOUND);
  }

  function replace(booking: ManagedReservation) {
    data = {
      ...data,
      reservations: data.reservations.map((b) => (b.id === booking.id ? booking : b)),
    };
  }

  function summary(booking: ManagedReservation): string {
    const day = dayWord(booking.date, today);
    return `${booking.id}, ${booking.guest_name}, ${guests(booking.party_size)}, ${day} ${booking.time}`;
  }

  function nextId(): string {
    const last = Math.max(0, ...data.reservations.map((b) => Number(b.id.slice(2))));
    return `R-${String(last + 1).padStart(3, "0")}`;
  }

  function upcoming(): ManagedReservation[] {
    const end = addDays(today, DAYS_AHEAD - 1);
    return data.reservations
      .filter((b) => b.date >= today && b.date <= end)
      .toSorted((a, b) => `${a.date}${a.time}`.localeCompare(`${b.date}${b.time}`));
  }

  function checkServices(services: Service[]) {
    if (services.length === 0) fail(422, "invalid_input", "Keep at least one service.");
    services.forEach((service, index) => {
      if (minutes(service.end) - minutes(service.start) < LAST_SEATING_MIN) {
        invalid("service_too_short", "A service lasts at least an hour.", { services: [index] });
      }
    });
    services.forEach((a, i) =>
      services.forEach((b, j) => {
        if (i < j && a.start < b.end && b.start < a.end) {
          invalid("services_overlap", "Services must not overlap.", { services: [i, j] });
        }
      }),
    );
  }

  function checkTables(tables: TableSpec[]) {
    if (tables.length === 0) invalid("no_tables", "At least one table is required.");
    const labels = new Set<string>();
    for (const table of tables) {
      if (labels.has(table.label)) {
        invalid("duplicate_table", "Table labels must be unique.", { table: table.label });
      }
      labels.add(table.label);
      if (!(table.seats >= 1)) {
        invalid("table_without_seats", "Every table needs a seat.", { table: table.label });
      }
    }
    for (const booking of data.reservations) {
      const kept = tables.find((table) => table.label === booking.table_label);
      const guest = booking.guest_name;
      if (!kept) {
        invalid("table_in_use", "A booking refers to this table.", {
          table: booking.table_label,
          guest,
        });
      }
      const ahead = booking.status === "confirmed" && !isPast(instant(booking.date, booking.time));
      if (ahead && kept.seats < booking.party_size) {
        invalid("table_too_small", "A booking needs more seats at this table.", {
          table: kept.label,
          guest,
          seats: booking.party_size,
        });
      }
    }
  }

  function blockedAhead() {
    return data.blocked
      .filter((b) => !isPast(b.starts_at))
      .toSorted((a, b) => a.starts_at.localeCompare(b.starts_at));
  }

  return {
    merchant: () => respond(data.merchant),

    dashboard() {
      const current = liveByVia(today);
      const vias = new Set<Via>([...data.channels.map((c) => c.via), ...current.keys()]);
      const channels = [...vias].map((via) => ({
        via,
        bookings:
          (data.channels.find((c) => c.via === via)?.bookings ?? 0) +
          (current.get(via) ?? 0) -
          (baseline.get(via) ?? 0),
      }));
      const day = (date: string) => data.reservations.filter((b) => b.date === date);
      const live = (date: string) => day(date).filter((b) => b.status === "confirmed");
      const sum = (bookings: ManagedReservation[]) =>
        bookings.reduce((total, b) => total + b.party_size, 0);
      const history = data.history.map((entry) =>
        entry.date === today
          ? {
              date: today,
              bookings: live(today).length,
              guests: sum(live(today)),
              agent_guests: sum(live(today).filter((b) => b.via === "claude")),
              cancelled: day(today).length - live(today).length,
            }
          : entry,
      );
      const nextDays = Array.from({ length: DAYS_AHEAD }, (_, index) => {
        const date = addDays(today, index);
        return { date, guests: sum(live(date)) };
      });
      return respond({
        date: today,
        history,
        next_days: nextDays,
        channels,
        room: room(),
        activity: data.activity.slice(0, ACTIVITY_LIMIT),
      });
    },

    reservations: (_slug, date) =>
      respond({
        date,
        services: data.hours.services,
        slot_length_min: data.rules.slot_length_min,
        largest_group: largestGroup(),
        blocked: data.blocked.filter((b) => b.date === date).map((b) => b.time),
        reservations: data.reservations
          .filter((b) => b.date === date)
          .toSorted((a, b) => a.time.localeCompare(b.time) || a.id.localeCompare(b.id)),
      }),

    async addBooking(_slug, { starts_at, party_size, guest_name, idempotency_key }) {
      const replayed = keys.get(idempotency_key);
      if (replayed) return respond(find(replayed));
      const name = guest_name.trim();
      if (!name) fail(422, "invalid_input", "Add the guest's name.");
      if (!(party_size >= 1)) invalid("group_too_small", "A booking needs at least one guest.");
      if (party_size > largestGroup()) {
        fail(422, "party_too_large", `Groups of up to ${largestGroup()} can book here.`);
      }
      const { date, time } = local(new Date(starts_at), data.merchant.timezone);
      const slot = slotsOf(date, party_size).find((s) => s.time === time);
      if (!slot?.table || slot.blocked) fail(409, "slot_unavailable", TAKEN);
      const booking: ManagedReservation = {
        id: nextId(),
        date,
        time,
        guest_name: name,
        party_size,
        table_label: slot.table.label,
        via: "merchant",
        status: "confirmed",
        cancelled_by: null,
        created_at: nowIso(),
      };
      data = { ...data, reservations: [...data.reservations, booking] };
      keys.set(idempotency_key, booking.id);
      log("new_booking", summary(booking), "merchant", booking.id);
      return respond(booking);
    },

    async moveBooking(_slug, id, { starts_at }) {
      const booking = find(id);
      if (booking.status === "cancelled") fail(409, "slot_unavailable", "This booking is cancelled.");
      const { date, time } = local(new Date(starts_at), data.merchant.timezone);
      if (date !== booking.date) fail(422, "invalid_input", "A booking moves within its day.");
      const slot = slotsOf(date, booking.party_size, booking.id).find((s) => s.time === time);
      if (!slot?.table || slot.blocked) fail(409, "slot_unavailable", TAKEN);
      const moved = { ...booking, time, table_label: slot.table.label };
      replace(moved);
      log("moved", summary(moved), "merchant", moved.id);
      return respond(moved);
    },

    async cancelBooking(_slug, id) {
      const booking = find(id);
      if (booking.status === "cancelled") {
        const outcome = booking.cancelled_by === "guest" ? "guest_cancelled" : "already_cancelled";
        return respond({ reservation: booking, outcome });
      }
      const ends = Date.parse(instant(booking.date, booking.time)) + data.rules.slot_length_min * 60_000;
      const outcome =
        ends <= now().getTime() ? "ended" : booking.via === "merchant" ? "own_booking" : "notified";
      const cancelled: ManagedReservation = {
        ...booking,
        status: "cancelled",
        cancelled_by: "merchant",
      };
      replace(cancelled);
      log("cancelled", summary(cancelled), "merchant", cancelled.id);
      return respond({ reservation: cancelled, outcome });
    },

    slots: (_slug, { date, partySize, ignore }) =>
      respond({ date, party_size: partySize, slots: slotsOf(date, partySize, ignore) }),

    guests(_slug, q) {
      const wanted = fold(q);
      const rows = new Map<string, { first: ManagedReservation; bookings: number }>();
      for (const booking of upcoming()) {
        const ahead = !isPast(instant(booking.date, booking.time));
        if (booking.status !== "confirmed" || !ahead) continue;
        const key = fold(booking.guest_name);
        if (!key.includes(wanted)) continue;
        const row = rows.get(key);
        rows.set(key, row ? { ...row, bookings: row.bookings + 1 } : { first: booking, bookings: 1 });
      }
      const guests = [...rows.values()]
        .map(({ first, bookings }) => ({
          guest_name: first.guest_name,
          reservation_id: first.id,
          date: first.date,
          time: first.time,
          party_size: first.party_size,
          bookings,
          via: first.via,
        }))
        .toSorted((a, b) => a.guest_name.localeCompare(b.guest_name));
      return respond({ guests });
    },

    search(_slug, q) {
      const wanted = fold(q);
      const found = wanted
        ? upcoming().filter(
            (b) => fold(b.guest_name).includes(wanted) || fold(b.id).includes(wanted),
          )
        : [];
      return respond({ reservations: found.slice(0, SEARCH_LIMIT) });
    },

    hours: () => respond(data.hours),

    async saveHours(_slug, { services }) {
      checkServices(services);
      data = { ...data, hours: { services } };
      log("saved", "Opening hours", "merchant");
      return respond(data.hours);
    },

    tables: () => respond({ tables: data.tables }),

    async saveTables(_slug, tables) {
      checkTables(tables);
      data = { ...data, tables };
      log("saved", "Floor plan", "merchant");
      return respond({ tables: data.tables });
    },

    rules: () => respond(data.rules),

    async saveRules(_slug, { max_party_size, fallback_url, cancellation_policy = null }) {
      if (!(max_party_size >= 1)) invalid("group_too_small", "The largest group is at least 1.");
      if (new URL(fallback_url).host !== new URL(data.merchant.website).host) {
        invalid("booking_page_off_site", "The booking page stays on your website.");
      }
      data = { ...data, rules: { ...data.rules, max_party_size, fallback_url, cancellation_policy } };
      log("saved", "Booking rules", "merchant");
      return respond(data.rules);
    },

    settings: () => respond(data.settings),

    async saveSettings(_slug, { name, city = null, price_level, tags }) {
      if (!name.trim()) invalid("name_required", "Your restaurant needs a name.");
      const changes = { name: name.trim(), city: city?.trim() || null, price_level, tags };
      data = {
        ...data,
        settings: { ...data.settings, ...changes },
        merchant: { ...data.merchant, ...changes },
      };
      log("saved", "Settings", "merchant");
      return respond(data.settings);
    },

    blocked: () => respond({ blocked: blockedAhead() }),

    async setBlocked(_slug, date, times) {
      const before = new Set(data.blocked.filter((b) => b.date === date).map((b) => b.time));
      const after = new Set(times);
      const label = (time: string) => `${dayWord(date, today)} ${time}`;
      for (const time of after) if (!before.has(time)) log("blocked", label(time), "merchant");
      for (const time of before) if (!after.has(time)) log("reopened", label(time), "merchant");
      const kept = data.blocked.filter((b) => b.date !== date);
      const added = [...after].map((time) => ({ starts_at: instant(date, time), date, time }));
      data = { ...data, blocked: [...kept, ...added] };
      return respond({ blocked: blockedAhead() });
    },

    async pauseAgent() {
      data = { ...data, merchant: { ...data.merchant, status: "down" } };
      log("paused", "Guests get your booking page.", "merchant");
      return respond({ status: data.merchant.status });
    },

    async resumeAgent() {
      data = { ...data, merchant: { ...data.merchant, status: "live" } };
      log("resumed", "Your agent takes bookings again.", "merchant");
      return respond({ status: data.merchant.status });
    },

    agentRequests: (_slug, { date, refused }) =>
      respond({
        requests: data.agent_requests
          .filter((r) => r.at.slice(0, 10) === date && (!refused || r.refused))
          .slice(0, REQUESTS_LIMIT),
      }),

    subscribe(_slug, { onEvent, onConnection }) {
      const timers = [setTimeout(() => onConnection(true), latencyMs)];
      if (!notified) {
        timers.push(
          setTimeout(() => {
            notified = true;
            const event = data.live_booking;
            const booking: ManagedReservation = {
              ...event.reservation,
              date: event.date,
              cancelled_by: null,
              created_at: nowIso(),
            };
            data = { ...data, reservations: [...data.reservations, booking] };
            log("new_booking", summary(booking), event.via, booking.id);
            onEvent(structuredClone(event));
          }, liveBookingDelayMs),
        );
      }
      return () => timers.forEach((timer) => clearTimeout(timer));
    },
  };
}

function minutes(time: string): number {
  const [hours, mins] = time.split(":").map(Number);
  return hours * 60 + mins;
}

function hhmm(total: number): string {
  const wrapped = ((total % 1440) + 1440) % 1440;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(wrapped / 60))}:${pad(wrapped % 60)}`;
}

function addDays(date: string, days: number): string {
  const [year, month, day] = date.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

// The local day and time of an instant in the merchant's timezone.
function local(moment: Date, timeZone: string): { date: string; time: string } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(moment)
      .map((part) => [part.type, part.value]),
  );
  return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}` };
}

// "today", "tomorrow", else "Tue 3 Nov", as the server writes activity texts.
function dayWord(date: string, today: string): string {
  if (date === today) return "today";
  if (date === addDays(today, 1)) return "tomorrow";
  const [year, month, day] = date.split("-").map(Number);
  return new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  })
    .format(new Date(Date.UTC(year, month - 1, day)))
    .replace(",", "");
}

function guests(count: number): string {
  return count === 1 ? "1 guest" : `${count} guests`;
}

// `same_guest`'s comparison (agent/service.py): runs of spaces are one, case folded.
function fold(text: string): string {
  return text.trim().split(/\s+/).join(" ").toLowerCase();
}
