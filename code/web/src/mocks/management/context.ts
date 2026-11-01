// The state every route group of the mock management client shares (#129): the demo's data,
// its running clock, and the answers' delay. `data` is replaced on each change, never edited.
import type { LiveHandlers } from "../../chat/client.ts";
import { ManagementRequestError } from "../../management/client.ts";
import type {
  ActivityItem,
  ActivityKind,
  ConversationItem,
  ErrorCode,
  ManagedReservation,
  ManagementFixture,
  Problem,
  SitePage,
  Via,
} from "../../types.ts";

const LATENCY_MS = 120;
const LIVE_BOOKING_DELAY_MS = 6000;

export type MockOptions = {
  latencyMs?: number;
  liveBookingDelayMs?: number;
  // The assistant's opening conversation (#140): the demo's, from #131's fixture.
  conversation?: readonly ConversationItem[];
  // What GET /site answers: the mock has no crawl (#151).
  sitePages?: readonly SitePage[];
};

export type Mock = {
  data: ManagementFixture;
  // The fixture's day: "today" for the whole run.
  readonly today: string;
  readonly latencyMs: number;
  readonly liveBookingDelayMs: number;
  readonly conversation: readonly ConversationItem[];
  readonly sitePages: readonly SitePage[];
  // Applied idempotency keys of the bookings the merchant adds.
  readonly keys: Map<string, { payload: string; id: string }>;
  // Today's bookings per channel in the fixture, which its channel counts already hold.
  readonly baseline: Map<Via, number>;
  readonly listeners: Set<LiveHandlers>;
  liveTimer: ReturnType<typeof setTimeout> | undefined;
  delivered: boolean;
  lastActivity: number;
  tries: number;
  now: () => Date;
  // The instant of a local day and time.
  instant: (date: string, time: string) => string;
  nowIso: () => string;
  isPast: (startsAt: string) => boolean;
  hasEnded: (booking: ManagedReservation) => boolean;
};

/**
 * The clock starts at `fixture.now` and runs; local times keep the offset of `fixture.now`, as
 * the demo week has no change of clock.
 */
export function createMock(fixture: ManagementFixture, options: MockOptions): Mock {
  const offset = fixture.now.match(/[+-]\d{2}:\d{2}$/)?.[0];
  if (!offset) throw new Error("fixture.now needs a ±HH:MM offset");
  const started = Date.now();
  const data = structuredClone(fixture);
  const now = () => new Date(Date.parse(fixture.now) + Date.now() - started);
  const instant = (date: string, time: string) => `${date}T${time}:00${offset}`;
  const today = fixture.now.slice(0, 10);
  const mock: Mock = {
    data,
    today,
    latencyMs: options.latencyMs ?? LATENCY_MS,
    liveBookingDelayMs: options.liveBookingDelayMs ?? LIVE_BOOKING_DELAY_MS,
    conversation: options.conversation ?? [],
    sitePages: options.sitePages ?? [],
    keys: new Map(),
    baseline: liveByVia(data.reservations, today),
    listeners: new Set(),
    liveTimer: undefined,
    delivered: false,
    lastActivity: Math.max(0, ...data.activity.map((item) => item.id)),
    tries: 0,
    now,
    instant,
    nowIso() {
      const { date, time, seconds } = local(now(), mock.data.merchant.timezone);
      return `${date}T${time}:${seconds}${offset}`;
    },
    isPast: (startsAt) => Date.parse(startsAt) <= now().getTime(),
    hasEnded: (booking) =>
      Date.parse(instant(booking.date, booking.time)) + mock.data.rules.slot_length_min * 60_000 <=
      now().getTime(),
  };
  return mock;
}

/** An answer after the mock's delay, as a copy: the caller can't change the mock's data. */
export function respond<T>(mock: Mock, value: T): Promise<T> {
  const copy = structuredClone(value);
  return new Promise((resolve) => setTimeout(() => resolve(copy), mock.latencyMs));
}

export function fail(mock: Mock, status: number, error: ErrorCode, message: string): never {
  const fallback = error === "party_too_large" ? { fallback_url: mock.data.rules.fallback_url } : {};
  throw new ManagementRequestError(status, { error, message, ...fallback });
}

export function invalid(message: string): never {
  throw new ManagementRequestError(422, { error: "invalid_input", message });
}

/** A form's refusal: the rule it broke, and where (core/forms.py). */
export function problem(message: string, details: Problem): never {
  throw new ManagementRequestError(422, { error: "invalid_input", message, problem: details });
}

/** A line of Home's activity, newest first. */
export function log(
  mock: Mock,
  kind: ActivityKind,
  line: string,
  source: Via,
  reservation: string | null = null,
): void {
  mock.lastActivity += 1;
  const item: ActivityItem = {
    id: mock.lastActivity,
    at: mock.nowIso(),
    kind,
    text: line,
    source,
    reservation_id: reservation,
  };
  mock.data = { ...mock.data, activity: [item, ...mock.data.activity] };
}

/** The day's confirmed bookings per channel. */
export function liveByVia(reservations: readonly ManagedReservation[], date: string): Map<Via, number> {
  const counts = new Map<Via, number>();
  for (const booking of reservations) {
    if (booking.date === date && booking.status === "confirmed") {
      counts.set(booking.via, (counts.get(booking.via) ?? 0) + 1);
    }
  }
  return counts;
}

/** The local day and time of an instant in the merchant's timezone. */
export function local(moment: Date, timeZone: string): { date: string; time: string; seconds: string } {
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
