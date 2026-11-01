// The Reservations and Guests pages' routes (#134, #144), and the day's blocked times (#133).
import type { ManagementClient } from "../../management/client.ts";
import type {
  BlockedTimes,
  GuestRow,
  ManagedReservation,
  MerchantBookingInput,
  MerchantCancelOutput,
  MoveBookingInput,
  ReservationDay,
} from "../../types.ts";
import {
  blockedAhead,
  dayStarts,
  dayWord,
  find,
  fold,
  largestGroup,
  nextId,
  replace,
  services,
  slotsOf,
  summary,
  upcoming,
} from "./bookings.ts";
import { type Mock, fail, invalid, local, log, respond } from "./context.ts";
import { HHMM, MAX_DAY_STARTS, MAX_GUEST_TEXT, aware, exact, required } from "./input.ts";

const SEARCH_LIMIT = 8;
const TAKEN = "That time is no longer free.";

type ReservationRoutes = Pick<
  ManagementClient,
  | "reservations"
  | "addBooking"
  | "moveBooking"
  | "cancelBooking"
  | "slots"
  | "guests"
  | "search"
  | "blocked"
  | "setBlocked"
>;

export function reservationRoutes(mock: Mock): ReservationRoutes {
  return {
    reservations: (_slug, date) => respond(mock, dayOf(mock, date)),
    addBooking: async (_slug, body) => respond(mock, addBooking(mock, body)),
    moveBooking: async (_slug, id, body) => respond(mock, moveBooking(mock, id, body)),
    cancelBooking: async (_slug, id) => respond(mock, cancelBooking(mock, id)),
    slots: (_slug, { date, partySize, ignore }) =>
      respond(mock, { date, party_size: partySize, slots: slotsOf(mock, date, partySize, ignore) }),
    guests: (_slug, q) => respond(mock, { guests: guestsOf(mock, q) }),
    search(_slug, q) {
      const wanted = fold(q);
      const found = wanted
        ? upcoming(mock).filter(
            (b) => fold(b.guest_name).includes(wanted) || fold(b.id).includes(wanted),
          )
        : [];
      return respond(mock, { reservations: found.slice(0, SEARCH_LIMIT) });
    },
    blocked: () => respond(mock, { blocked: blockedAhead(mock) }),
    setBlocked: async (_slug, date, times) => respond(mock, setBlocked(mock, date, times)),
  };
}

function dayOf(mock: Mock, date: string): ReservationDay {
  const { data } = mock;
  return {
    date,
    services: services(mock),
    starts: dayStarts(mock),
    slot_length_min: data.rules.slot_length_min,
    largest_group: largestGroup(mock),
    blocked: data.blocked
      .filter((b) => b.date === date)
      .map((b) => b.time)
      .toSorted(),
    reservations: data.reservations
      .filter((b) => b.date === date)
      .toSorted((a, b) => a.time.localeCompare(b.time) || a.id.localeCompare(b.id)),
  };
}

function addBooking(mock: Mock, body: MerchantBookingInput): ManagedReservation {
  exact(body, ["starts_at", "party_size", "guest_name", "idempotency_key"]);
  const startsAt = aware(body.starts_at);
  const name = required(body.guest_name, "guest_name", MAX_GUEST_TEXT);
  const key = required(body.idempotency_key, "idempotency_key", MAX_GUEST_TEXT);
  const partySize = body.party_size;
  if (!Number.isInteger(partySize) || partySize < 1) {
    invalid("party_size: Input should be greater than 0");
  }
  const payload = JSON.stringify([Date.parse(startsAt), partySize, name]);
  const replayed = mock.keys.get(key);
  if (replayed) {
    if (replayed.payload !== payload) {
      invalid("idempotency_key reused with different parameters");
    }
    return find(mock, replayed.id);
  }
  if (mock.isPast(startsAt)) fail(mock, 409, "slot_unavailable", "That time has passed.");
  const largest = largestGroup(mock);
  if (partySize > largest) fail(mock, 422, "party_too_large", `Groups of up to ${largest} can book here.`);
  const { date, time } = local(new Date(startsAt), mock.data.merchant.timezone);
  const slot = slotsOf(mock, date, partySize).find((s) => s.time === time);
  if (!slot?.table || slot.blocked) return fail(mock, 409, "slot_unavailable", TAKEN);
  const booking: ManagedReservation = {
    id: nextId(mock),
    date,
    time,
    guest_name: name,
    party_size: partySize,
    table_label: slot.table.label,
    via: "merchant",
    status: "confirmed",
    cancelled_by: null,
    created_at: mock.nowIso(),
  };
  mock.data = { ...mock.data, reservations: [...mock.data.reservations, booking] };
  mock.keys.set(key, { payload, id: booking.id });
  log(mock, "new_booking", summary(mock, booking), "merchant", booking.id);
  return booking;
}

function moveBooking(mock: Mock, id: string, body: MoveBookingInput): ManagedReservation {
  exact(body, ["starts_at", "idempotency_key"]);
  const startsAt = aware(body.starts_at);
  required(body.idempotency_key, "idempotency_key", MAX_GUEST_TEXT);
  const booking = find(mock, id);
  if (booking.status === "cancelled") {
    fail(mock, 409, "slot_unavailable", "This booking is cancelled.");
  }
  if (mock.isPast(mock.instant(booking.date, booking.time))) {
    fail(mock, 409, "slot_unavailable", "This booking has already started.");
  }
  const { date, time } = local(new Date(startsAt), mock.data.merchant.timezone);
  if (date !== booking.date) invalid("A booking moves within its day.");
  const slot = slotsOf(mock, date, booking.party_size, booking.id).find((s) => s.time === time);
  if (!slot?.table || slot.blocked) return fail(mock, 409, "slot_unavailable", TAKEN);
  const moved = { ...booking, time, table_label: slot.table.label };
  replace(mock, moved);
  log(mock, "moved", summary(mock, moved), "merchant", moved.id);
  return moved;
}

function cancelBooking(mock: Mock, id: string): MerchantCancelOutput {
  const booking = find(mock, id);
  if (booking.status === "cancelled") {
    const outcome = booking.cancelled_by === "guest" ? "guest_cancelled" : "already_cancelled";
    return { reservation: booking, outcome };
  }
  const outcome = mock.hasEnded(booking)
    ? "ended"
    : booking.via === "merchant"
      ? "own_booking"
      : "notified";
  const cancelled: ManagedReservation = { ...booking, status: "cancelled", cancelled_by: "merchant" };
  replace(mock, cancelled);
  log(mock, "cancelled", summary(mock, cancelled), "merchant", cancelled.id);
  return { reservation: cancelled, outcome };
}

// The guests with a booking still to come, one row each with their first one.
function guestsOf(mock: Mock, q: string): GuestRow[] {
  const wanted = fold(q);
  const rows = new Map<string, { first: ManagedReservation; bookings: number }>();
  for (const booking of upcoming(mock)) {
    const ahead = !mock.isPast(mock.instant(booking.date, booking.time));
    if (booking.status !== "confirmed" || !ahead) continue;
    const key = fold(booking.guest_name);
    if (!key.includes(wanted)) continue;
    const row = rows.get(key);
    rows.set(key, row ? { ...row, bookings: row.bookings + 1 } : { first: booking, bookings: 1 });
  }
  return [...rows.values()]
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
}

// agent/admin.py `set_blocked`: only starts still to come change; the day's past blocks stay
// and a past time sent is ignored.
function setBlocked(mock: Mock, date: string, times: string[]): BlockedTimes {
  if (times.length > MAX_DAY_STARTS) invalid(`times: at most ${MAX_DAY_STARTS}`);
  if (times.some((time) => !HHMM.test(time))) invalid("times: HH:MM times");
  const starts = dayStarts(mock);
  const sent = [...new Set(times)].toSorted();
  const unknown = sent.filter((time) => !starts.includes(time));
  if (unknown.length) {
    const valid = starts.join(", ") || "none";
    invalid(`${unknown.join(", ")} is not a start time on ${date}; valid times: ${valid}`);
  }
  const wanted = sent.filter((time) => !mock.isPast(mock.instant(date, time)));
  const day = mock.data.blocked.filter((b) => b.date === date);
  const added = wanted.filter((time) => !day.some((b) => b.time === time));
  const removed = day
    .filter((b) => !mock.isPast(b.starts_at) && !wanted.includes(b.time))
    .map((b) => b.time)
    .toSorted();
  // One activity line per save (#133).
  const word = dayWord(date, mock.today);
  if (added.length) log(mock, "blocked", `${word} ${added.join(", ")}`, "merchant");
  else if (removed.length) log(mock, "reopened", `${word} ${removed.join(", ")}`, "merchant");
  const kept = mock.data.blocked.filter((b) => b.date !== date || mock.isPast(b.starts_at));
  const set = wanted.map((time) => ({ starts_at: mock.instant(date, time), date, time }));
  mock.data = { ...mock.data, blocked: [...kept, ...set] };
  return { blocked: blockedAhead(mock) };
}
