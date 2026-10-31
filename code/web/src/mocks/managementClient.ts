import type { LiveHandlers } from "../chat/client.ts";
import {
  ManagementRequestError,
  type ManagementClient,
} from "../management/client.ts";
import type {
  ActivityItem,
  ActivityKind,
  ConversationItem,
  DaySlot,
  ErrorCode,
  GeneratedTool,
  ManagedReservation,
  ManagementFixture,
  Problem,
  Room,
  Service,
  Tag,
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
// The bounds of core/models.py and core/forms.py.
const MAX_GUEST_TEXT = 200;
const MAX_SHORT_TEXT = 80;
const MAX_POLICY_TEXT = 500;
const MAX_TABLE_LABEL = 20;
const MAX_SERVICES = 8;
const MAX_TABLES = 100;
const MAX_DAY_STARTS = 48;
const MAX_SEATS = 1000;
const TAGS: readonly Tag[] = [
  "gluten-free",
  "vegetarian",
  "vegan",
  "halal",
  "kosher",
  "terrace",
  "kid-friendly",
];
const PRICES = ["€", "€€", "€€€", "€€€€"];
// gateway/merchant_server.py: an AI agent never reads the merchant's own policy.
const POLICY_ELSEWHERE = "see the restaurant's own website";
const HHMM = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
const INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;
// Unicode category Cc, which core/models.py refuses in any text.
// oxlint-disable-next-line no-control-regex -- finding them is the point
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;
// What Python's str.isprintable refuses, which agent/admin.py asks of a table label.
const UNPRINTABLE = /(?! )[\p{C}\p{Z}]/u;
const NOT_FOUND = "No booking with that reference.";
const TAKEN = "That time is no longer free.";

type Options = {
  latencyMs?: number;
  liveBookingDelayMs?: number;
  // The assistant's opening conversation (#140): the demo's, from #131's fixture.
  conversation?: readonly ConversationItem[];
};

// The management site against the demo fixture, with no platform (VITE_MOCK=1).
// It applies the server's rules (#133, #134), not the mockup's: past times are
// refused, bodies hold only their input's fields, forms answer the `Problem`.
// The clock starts at `fixture.now` and runs; local times keep the offset of
// `fixture.now`, as the demo week has no change of clock.
export function createMockManagementClient(
  fixture: ManagementFixture,
  {
    latencyMs = LATENCY_MS,
    liveBookingDelayMs = LIVE_BOOKING_DELAY_MS,
    conversation = [],
  }: Options = {},
): ManagementClient {
  const offset = fixture.now.match(/[+-]\d{2}:\d{2}$/)?.[0];
  if (!offset) throw new Error("fixture.now needs a ±HH:MM offset");
  const started = Date.now();
  const today = fixture.now.slice(0, 10);
  let data = structuredClone(fixture);
  let lastActivity = Math.max(0, ...data.activity.map((item) => item.id));
  const keys = new Map<string, { payload: string; id: string }>();
  const baseline = liveByVia(today);
  const listeners = new Set<LiveHandlers>();
  let liveTimer: ReturnType<typeof setTimeout> | undefined;
  let delivered = false;

  const now = () => new Date(Date.parse(fixture.now) + Date.now() - started);
  const instant = (date: string, time: string) => `${date}T${time}:00${offset}`;
  const nowIso = () => {
    const { date, time, seconds } = local(now(), data.merchant.timezone);
    return `${date}T${time}:${seconds}${offset}`;
  };
  const isPast = (startsAt: string) => Date.parse(startsAt) <= now().getTime();
  const hasEnded = (booking: ManagedReservation) =>
    Date.parse(instant(booking.date, booking.time)) + data.rules.slot_length_min * 60_000 <=
    now().getTime();

  function respond<T>(value: T): Promise<T> {
    const copy = structuredClone(value);
    return new Promise((resolve) => setTimeout(() => resolve(copy), latencyMs));
  }

  function fail(status: number, error: ErrorCode, message: string, problem?: Problem): never {
    const fallback =
      error === "party_too_large" ? { fallback_url: data.rules.fallback_url } : {};
    const details = problem ? { problem } : {};
    throw new ManagementRequestError(status, { error, message, ...fallback, ...details });
  }

  function invalid(message: string): never {
    return fail(422, "invalid_input", message);
  }

  function problem(message: string, details: Problem): never {
    return fail(422, "invalid_input", message, details);
  }

  // The platform's models forbid unknown fields.
  function exact(body: object, allowed: readonly string[]) {
    const extra = Object.keys(body).filter((key) => !allowed.includes(key));
    if (extra.length) invalid(`${extra.join(", ")}: Extra inputs are not permitted`);
  }

  // A stripped text without a control character; blank is null unless required.
  function text(value: unknown, field: string, max: number, required: boolean): string | null {
    if (value === null || value === undefined) {
      return required ? invalid(`${field}: Field required`) : null;
    }
    if (typeof value !== "string") return invalid(`${field}: a string is required`);
    const trimmed = value.trim();
    if (CONTROL.test(trimmed)) invalid(`${field}: must not contain a control character`);
    if (trimmed.length > max) invalid(`${field}: at most ${max} characters`);
    if (!trimmed && required) invalid(`${field}: must not be blank`);
    return trimmed || null;
  }

  function required(value: unknown, field: string, max: number): string {
    return text(value, field, max, true) ?? invalid(`${field}: Field required`);
  }

  function aware(value: unknown): string {
    if (typeof value !== "string" || !INSTANT.test(value) || Number.isNaN(Date.parse(value))) {
      return invalid("starts_at: a time with its UTC offset is required");
    }
    // BookingTime (core/models.py): between 2000-01-01 and 2099-12-31.
    const year = Number(value.slice(0, 4));
    if (year < 2000 || year > 2099) invalid("starts_at: must be between 2000-01-01 and 2099-12-31");
    return value;
  }

  function log(kind: ActivityKind, line: string, source: Via, reservation: string | null = null) {
    lastActivity += 1;
    const item: ActivityItem = {
      id: lastActivity,
      at: nowIso(),
      kind,
      text: line,
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
    const largestTable = Math.max(...data.tables.map((table) => table.seats));
    return Math.min(data.rules.max_party_size, largestTable);
  }

  function services(): Service[] {
    return data.hours.services.toSorted((a, b) => a.start.localeCompare(b.start));
  }

  function startsOf(): string[] {
    const starts: string[] = [];
    for (const service of services()) {
      const last = minutes(service.end) - LAST_SEATING_MIN;
      for (let start = minutes(service.start); start <= last; start += STEP_MIN) {
        starts.push(hhmm(start));
      }
    }
    return starts;
  }

  function slotsOf(date: string, partySize: number, ignore?: string): DaySlot[] {
    const length = data.rules.slot_length_min;
    const blocked = new Set(data.blocked.filter((b) => b.date === date).map((b) => b.time));
    const live = data.reservations.filter(
      (b) => b.date === date && b.status === "confirmed" && b.id !== ignore,
    );
    // Smallest fitting table, then label (core/availability.py `pick_table`).
    const fitting =
      partySize > data.rules.max_party_size
        ? []
        : data.tables
            .filter((table) => table.seats >= partySize)
            .toSorted((a, b) => a.seats - b.seats || (a.label < b.label ? -1 : 1));
    return startsOf()
      .filter((time) => !isPast(instant(date, time)))
      .map((time) => {
        const start = minutes(time);
        const table = fitting.find(
          (candidate) =>
            !live.some(
              (b) =>
                b.table_label === candidate.label &&
                minutes(b.time) < start + length &&
                start < minutes(b.time) + length,
            ),
        );
        return {
          starts_at: instant(date, time),
          time,
          until: hhmm(start + length),
          table: table ?? null,
          blocked: blocked.has(time),
        };
      });
  }

  function isFree(date: string, time: string, label: string): boolean {
    const length = data.rules.slot_length_min;
    const start = minutes(time);
    return !data.reservations.some(
      (b) =>
        b.date === date &&
        b.status === "confirmed" &&
        b.table_label === label &&
        minutes(b.time) < start + length &&
        start < minutes(b.time) + length,
    );
  }

  function room(): Room {
    const clock = local(now(), data.merchant.timezone);
    const sorted = services();
    const service =
      sorted.find((s) => minutes(s.end) > minutes(clock.time)) ?? sorted[sorted.length - 1];
    const within = (time: string) =>
      minutes(time) >= minutes(service.start) && minutes(time) < minutes(service.end);
    const live = data.reservations.filter(
      (b) => b.date === clock.date && b.status === "confirmed" && within(b.time),
    );
    const booked = new Set(live.map((b) => b.table_label));
    const largest = largestGroup();
    const partySize = Math.min(ROOM_PARTY, largest);
    const next = slotsOf(clock.date, partySize).find(
      (s) => !s.blocked && s.table && within(s.time),
    );
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

  // What an AI agent reads first on /m/<slug>/mcp (agent/templates/restaurant.json).
  function tools(): GeneratedTool[] {
    const { name, city } = data.settings;
    const hours = services()
      .map((s) => `${s.start}-${s.end}`)
      .join(", ");
    const groups = data.rules.max_party_size;
    return [
      {
        name: "get_availability",
        description: `Check free tables at ${name} (${city ?? "unknown city"}). Services: ${hours}. Groups up to ${groups}.`,
      },
      {
        name: "create_reservation",
        description: `Book a table at ${name}. Always confirm with the guest first.`,
      },
      { name: "update_reservation", description: `Move or resize a booking at ${name}.` },
      {
        name: "cancel_reservation",
        description: `Cancel a booking at ${name}. Policy: ${POLICY_ELSEWHERE}.`,
      },
    ];
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

  // The live booking's reference stays free until it arrives.
  function nextId(): string {
    const ids = [...data.reservations.map((b) => b.id), data.live_booking.reservation.id];
    const last = Math.max(0, ...ids.map((id) => Number(id.slice(2))));
    return `R-${String(last + 1).padStart(3, "0")}`;
  }

  function upcoming(): ManagedReservation[] {
    const end = addDays(today, DAYS_AHEAD - 1);
    return data.reservations
      .filter((b) => b.date >= today && b.date <= end)
      .toSorted((a, b) => `${a.date}${a.time}`.localeCompare(`${b.date}${b.time}`));
  }

  function blockedAhead() {
    return data.blocked
      .filter((b) => !isPast(b.starts_at))
      .toSorted((a, b) => a.starts_at.localeCompare(b.starts_at));
  }

  function withTools() {
    data = { ...data, rules: { ...data.rules, tools: tools() } };
  }

  // agent/admin.py `_check_plan_change`: of the bookings at a removed or shrunk
  // table, by start, the first one still running that the plan strands; else a
  // removed table that past or cancelled bookings refer to.
  function checkPlanChange(plan: { label: string; seats: number }[]) {
    const wanted = new Map(plan.map((table) => [table.label, table.seats]));
    const affected = new Set(
      data.tables.filter((t) => (wanted.get(t.label) ?? 0) < t.seats).map((t) => t.label),
    );
    const bookings = data.reservations
      .filter((b) => affected.has(b.table_label))
      .toSorted(
        (a, b) =>
          `${a.date}${a.time}`.localeCompare(`${b.date}${b.time}`) || (a.id < b.id ? -1 : 1),
      );
    const history = new Map<string, string>();
    for (const booking of bookings) {
      const { table_label: table, guest_name: guest } = booking;
      const seats = wanted.get(table);
      const running = booking.status === "confirmed" && !hasEnded(booking);
      if (seats === undefined) {
        if (running) {
          problem("A booking refers to this table.", { code: "table_in_use", table, guest });
        }
        if (!history.has(table)) history.set(table, guest);
      } else if (running && booking.party_size > seats) {
        problem("A booking needs more seats at this table.", {
          code: "table_too_small",
          table,
          guest,
          seats: booking.party_size,
        });
      }
    }
    const first = [...history.keys()].toSorted()[0];
    if (first !== undefined) {
      problem("Past bookings refer to this table.", {
        code: "table_in_use",
        table: first,
        guest: history.get(first),
      });
    }
  }

  // The agent's booking of the demo, once for every listener: it keeps the
  // fixture's table, unless the merchant took it meanwhile.
  function deliverLiveBooking() {
    delivered = true;
    const event = data.live_booking;
    const { reservation } = event;
    const slot = slotsOf(event.date, reservation.party_size).find(
      (s) => s.time === reservation.time,
    );
    if (!slot?.table || slot.blocked) return;
    const own = data.tables.find((table) => table.label === reservation.table_label);
    const table = own && isFree(event.date, reservation.time, own.label) ? own : slot.table;
    const booked = { ...event, reservation: { ...reservation, table_label: table.label } };
    const booking: ManagedReservation = {
      ...booked.reservation,
      date: booked.date,
      cancelled_by: null,
      created_at: nowIso(),
    };
    data = { ...data, reservations: [...data.reservations, booking] };
    log("new_booking", summary(booking), booked.via, booking.id);
    for (const listener of listeners) listener.onEvent(structuredClone(booked));
  }

  return {
    merchant: () => respond({ ...data.merchant, now: nowIso() }),
    assistant: () => respond({ session_id: "mock-owner", items: [...conversation] }),

    async dashboard() {
      const current = liveByVia(today);
      const vias = new Set<Via>([...data.channels.map((c) => c.via), ...current.keys()]);
      const channels = [...vias].map((via) => ({
        via,
        bookings:
          (data.channels.find((c) => c.via === via)?.bookings ?? 0) +
          (current.get(via) ?? 0) -
          (baseline.get(via) ?? 0),
      }));
      const day = (d: string) => data.reservations.filter((b) => b.date === d);
      const live = (d: string) => day(d).filter((b) => b.status === "confirmed");
      const sum = (bookings: ManagedReservation[]) =>
        bookings.reduce((total, b) => total + b.party_size, 0);
      const history = data.history.map((entry) =>
        entry.date === today
          ? {
              date: today,
              bookings: live(today).length,
              guests: sum(live(today)),
              agent_guests: sum(live(today).filter((b) => throughAgent(b.via))),
              cancelled: day(today).length - live(today).length,
            }
          : entry,
      );
      const nextDays = Array.from({ length: DAYS_AHEAD }, (_, index) => {
        const d = addDays(today, index);
        return { date: d, guests: sum(live(d)) };
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
        services: services(),
        starts: startsOf(),
        slot_length_min: data.rules.slot_length_min,
        largest_group: largestGroup(),
        blocked: data.blocked
          .filter((b) => b.date === date)
          .map((b) => b.time)
          .toSorted(),
        reservations: data.reservations
          .filter((b) => b.date === date)
          .toSorted((a, b) => a.time.localeCompare(b.time) || a.id.localeCompare(b.id)),
      }),

    async addBooking(_slug, body) {
      exact(body, ["starts_at", "party_size", "guest_name", "idempotency_key"]);
      const startsAt = aware(body.starts_at);
      const name = required(body.guest_name, "guest_name", MAX_GUEST_TEXT);
      const key = required(body.idempotency_key, "idempotency_key", MAX_GUEST_TEXT);
      const partySize = body.party_size;
      if (!Number.isInteger(partySize) || partySize < 1) {
        invalid("party_size: Input should be greater than 0");
      }
      const payload = JSON.stringify([Date.parse(startsAt), partySize, name]);
      const replayed = keys.get(key);
      if (replayed) {
        if (replayed.payload !== payload) {
          invalid("idempotency_key reused with different parameters");
        }
        return respond(find(replayed.id));
      }
      if (isPast(startsAt)) fail(409, "slot_unavailable", "That time has passed.");
      if (partySize > largestGroup()) {
        fail(422, "party_too_large", `Groups of up to ${largestGroup()} can book here.`);
      }
      const { date, time } = local(new Date(startsAt), data.merchant.timezone);
      const slot = slotsOf(date, partySize).find((s) => s.time === time);
      if (!slot?.table || slot.blocked) fail(409, "slot_unavailable", TAKEN);
      const booking: ManagedReservation = {
        id: nextId(),
        date,
        time,
        guest_name: name,
        party_size: partySize,
        table_label: slot.table.label,
        via: "merchant",
        status: "confirmed",
        cancelled_by: null,
        created_at: nowIso(),
      };
      data = { ...data, reservations: [...data.reservations, booking] };
      keys.set(key, { payload, id: booking.id });
      log("new_booking", summary(booking), "merchant", booking.id);
      return respond(booking);
    },

    async moveBooking(_slug, id, body) {
      exact(body, ["starts_at", "idempotency_key"]);
      const startsAt = aware(body.starts_at);
      required(body.idempotency_key, "idempotency_key", MAX_GUEST_TEXT);
      const booking = find(id);
      if (booking.status === "cancelled") {
        fail(409, "slot_unavailable", "This booking is cancelled.");
      }
      if (isPast(instant(booking.date, booking.time))) {
        fail(409, "slot_unavailable", "This booking has already started.");
      }
      const { date, time } = local(new Date(startsAt), data.merchant.timezone);
      if (date !== booking.date) invalid("A booking moves within its day.");
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
      const outcome = hasEnded(booking)
        ? "ended"
        : booking.via === "merchant"
          ? "own_booking"
          : "notified";
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
        rows.set(
          key,
          row ? { ...row, bookings: row.bookings + 1 } : { first: booking, bookings: 1 },
        );
      }
      const found = [...rows.values()]
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
      return respond({ guests: found });
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

    hours: () => respond({ services: services() }),

    async saveHours(_slug, body) {
      exact(body, ["services"]);
      if (body.services.length > MAX_SERVICES) invalid(`services: at most ${MAX_SERVICES}`);
      // DraftTime (core/models.py): blank is missing, else HH:MM, before any rule.
      const drafts = body.services.map((draft, index) => {
        exact(draft, ["start", "end"]);
        const time = (value: unknown) => {
          if (value === null || value === undefined) return null;
          if (typeof value !== "string") return invalid(`services.${index}: HH:MM times`);
          if (!value.trim()) return null;
          return HHMM.test(value) ? value : invalid(`services.${index}: HH:MM times`);
        };
        return { start: time(draft.start), end: time(draft.end) };
      });
      if (drafts.length === 0) problem("Keep at least one service.", { code: "no_services" });
      const saved = drafts.map(({ start, end }, index): Service => {
        if (start === null || end === null) {
          return problem("A service needs a start and an end.", {
            code: "service_incomplete",
            services: [index],
          });
        }
        if (minutes(end) - minutes(start) < LAST_SEATING_MIN) {
          problem("A service lasts at least 60 minutes.", {
            code: "service_too_short",
            services: [index],
          });
        }
        return { start, end };
      });
      saved.forEach((a, i) =>
        saved.forEach((b, j) => {
          if (i < j && a.start < b.end && b.start < a.end) {
            problem("Services must not overlap.", { code: "services_overlap", services: [i, j] });
          }
        }),
      );
      // Stored by start, as the server does: the same hours in another order are no change.
      const byStart = saved.toSorted((a, b) => a.start.localeCompare(b.start));
      const before = services();
      const changed =
        byStart.length !== before.length ||
        byStart.some((s, i) => s.start !== before[i]?.start || s.end !== before[i]?.end);
      data = { ...data, hours: { services: byStart } };
      withTools();
      if (changed) log("saved", "Opening hours", "merchant");
      return respond({ services: services() });
    },

    tables: () => respond({ tables: data.tables }),

    async saveTables(_slug, body) {
      exact(body, ["tables"]);
      if (body.tables.length > MAX_TABLES) invalid(`tables: at most ${MAX_TABLES}`);
      const tables = body.tables.map((draft) => {
        exact(draft, ["label", "seats"]);
        const label = required(draft.label, "label", MAX_TABLE_LABEL);
        if (!Number.isInteger(draft.seats)) invalid("seats: an integer is required");
        return { label, seats: draft.seats };
      });
      if (tables.length === 0) problem("Keep at least one table.", { code: "no_tables" });
      const labels = new Set<string>();
      for (const { label, seats } of tables) {
        if (labels.has(label)) {
          problem("Table labels must be unique.", { code: "duplicate_table", table: label });
        }
        labels.add(label);
        if (seats < 1) {
          problem("Every table needs at least 1 seat.", {
            code: "table_without_seats",
            table: label,
          });
        }
        if (seats > MAX_SEATS) {
          problem(`A table has at most ${MAX_SEATS} seats.`, {
            code: "too_many_seats",
            table: label,
            seats: MAX_SEATS,
          });
        }
      }
      const unprintable = tables.find((table) => UNPRINTABLE.test(table.label));
      if (unprintable) {
        const label = unprintable.label;
        invalid(`Invalid table '${label}': printable label and at most ${MAX_SEATS} seats`);
      }
      checkPlanChange(tables);
      const plan = (rows: { label: string; seats: number }[]) =>
        JSON.stringify(rows.map((t) => [t.label, t.seats]).toSorted());
      const changed = plan(tables) !== plan(data.tables);
      data = { ...data, tables };
      if (changed) log("saved", "Floor plan", "merchant");
      return respond({ tables: data.tables });
    },

    rules: () => respond(data.rules),

    async saveRules(_slug, body) {
      exact(body, ["max_party_size", "fallback_url", "cancellation_policy"]);
      const largest = body.max_party_size;
      if (!Number.isInteger(largest)) invalid("max_party_size: an integer is required");
      const page = parseUrl(body.fallback_url) ?? invalid("fallback_url: a URL is required");
      if (page.protocol !== "http:" && page.protocol !== "https:") {
        invalid("fallback_url: an http(s) URL is required");
      }
      const policy = text(body.cancellation_policy, "cancellation_policy", MAX_POLICY_TEXT, false);
      if (largest < 1) problem("The largest group is at least 1.", { code: "group_too_small" });
      if (largest > MAX_SEATS) {
        problem(`The largest group is at most ${MAX_SEATS}.`, {
          code: "too_many_seats",
          seats: MAX_SEATS,
        });
      }
      if (!sameSite(page, data.merchant.website)) {
        problem("The booking page stays on your website.", { code: "booking_page_off_site" });
      }
      const changes = {
        max_party_size: largest,
        fallback_url: page.href, // as HttpUrl normalises it
        cancellation_policy: policy,
      };
      const before = data.rules;
      const changed =
        changes.max_party_size !== before.max_party_size ||
        changes.fallback_url !== before.fallback_url ||
        changes.cancellation_policy !== before.cancellation_policy;
      data = { ...data, rules: { ...data.rules, ...changes } };
      withTools();
      if (changed) log("saved", "Booking rules", "merchant");
      return respond(data.rules);
    },

    settings: () => respond(data.settings),

    async saveSettings(_slug, body) {
      exact(body, ["name", "city", "price_level", "tags"]);
      if (typeof body.name !== "string") invalid("name: a string is required");
      const name = text(body.name, "name", MAX_SHORT_TEXT, false);
      const city = text(body.city, "city", MAX_SHORT_TEXT, false);
      if (!PRICES.includes(body.price_level)) invalid("price_level: one of €, €€, €€€, €€€€");
      if (body.tags.length > TAGS.length) invalid(`tags: at most ${TAGS.length}`);
      if (body.tags.some((tag) => !TAGS.includes(tag))) invalid("tags: unknown tag");
      if (name === null) problem("The restaurant needs a name.", { code: "name_required" });
      const changes = { name, city, price_level: body.price_level, tags: [...new Set(body.tags)] };
      const before = data.settings;
      const changed =
        name !== before.name ||
        city !== before.city ||
        changes.price_level !== before.price_level ||
        changes.tags.join() !== before.tags.join();
      data = {
        ...data,
        settings: { ...data.settings, ...changes },
        merchant: { ...data.merchant, ...changes },
      };
      withTools();
      if (changed) log("saved", "Settings", "merchant");
      return respond(data.settings);
    },

    blocked: () => respond({ blocked: blockedAhead() }),

    // agent/admin.py `set_blocked`: only starts still to come change; the day's
    // past blocks stay and a past time sent is ignored.
    async setBlocked(_slug, date, times) {
      if (times.length > MAX_DAY_STARTS) invalid(`times: at most ${MAX_DAY_STARTS}`);
      if (times.some((time) => !HHMM.test(time))) invalid("times: HH:MM times");
      const starts = startsOf();
      const sent = [...new Set(times)].toSorted();
      const unknown = sent.filter((time) => !starts.includes(time));
      if (unknown.length) {
        const valid = starts.join(", ") || "none";
        invalid(`${unknown.join(", ")} is not a start time on ${date}; valid times: ${valid}`);
      }
      const wanted = sent.filter((time) => !isPast(instant(date, time)));
      const day = data.blocked.filter((b) => b.date === date);
      const added = wanted.filter((time) => !day.some((b) => b.time === time));
      const removed = day
        .filter((b) => !isPast(b.starts_at) && !wanted.includes(b.time))
        .map((b) => b.time)
        .toSorted();
      // One activity line per save (#133).
      const word = dayWord(date, today);
      if (added.length) log("blocked", `${word} ${added.join(", ")}`, "merchant");
      else if (removed.length) log("reopened", `${word} ${removed.join(", ")}`, "merchant");
      const kept = data.blocked.filter((b) => b.date !== date || isPast(b.starts_at));
      const set = wanted.map((time) => ({ starts_at: instant(date, time), date, time }));
      data = { ...data, blocked: [...kept, ...set] };
      return respond({ blocked: blockedAhead() });
    },

    async publish() {
      if (data.merchant.status === "down") {
        invalid("Your agent is paused: resume it rather than publish it again.");
      }
      data = { ...data, merchant: { ...data.merchant, status: "live" } };
      return respond({ ...data.merchant, now: nowIso() });
    },

    async pauseAgent() {
      data = { ...data, merchant: { ...data.merchant, status: "down" } };
      log("paused", "Guests get your booking page.", "merchant");
      return respond({ status: data.merchant.status });
    },

    async resumeAgent() {
      // A merchant still building goes live by publishing it (#136), not here.
      if (data.merchant.status === "building") invalid("Publish your agent first.");
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

    subscribe(_slug, handlers) {
      listeners.add(handlers);
      const connected = setTimeout(() => handlers.onConnection(true), latencyMs);
      if (!delivered && liveTimer === undefined) {
        liveTimer = setTimeout(deliverLiveBooking, liveBookingDelayMs);
      }
      return () => {
        clearTimeout(connected);
        listeners.delete(handlers);
        if (listeners.size === 0 && !delivered) {
          clearTimeout(liveTimer);
          liveTimer = undefined;
        }
      };
    },
  };
}

// "Through an agent": any channel but the merchant itself and the website.
function throughAgent(via: Via): boolean {
  return via !== "merchant" && via !== "website";
}

function parseUrl(value: unknown): URL | null {
  try {
    return typeof value === "string" ? new URL(value) : null;
  } catch {
    return null;
  }
}

// agent/admin.py `_same_site`: the host, `www.` aside, and the port, the
// scheme's by default, so the demo sites differ only by their port.
function site(url: URL): string | null {
  const host = url.hostname.replace(/^www\./, "");
  return host ? `${host}:${url.port || (url.protocol === "https:" ? "443" : "80")}` : null;
}

function sameSite(page: URL, website: string): boolean {
  const home = parseUrl(website);
  return home !== null && site(page) !== null && site(page) === site(home);
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
function local(
  moment: Date,
  timeZone: string,
): { date: string; time: string; seconds: string } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(moment)
      .map((part) => [part.type, part.value]),
  );
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    time: `${parts.hour}:${parts.minute}`,
    seconds: parts.second,
  };
}

// "today", "tomorrow", else "Tue 3 Nov", as the server words activity lines.
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

// `guest_key` (agent/guests.py): runs of spaces are one, accents dropped, case folded.
function fold(text: string): string {
  return text.normalize("NFKD").replace(/\p{M}/gu, "").trim().split(/\s+/).join(" ").toLowerCase();
}
