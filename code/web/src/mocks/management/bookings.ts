// The bookings' rules (AGENTS.md §6) on the mock's data, as core/availability.py and
// agent/management.py apply them.
import { plural } from "../../cards/format.ts";
import { addDays, dayShort } from "../../management/dates.ts";
import { startsOf, toHHMM, toMinutes } from "../../management/services.ts";
import type { BlockedTime, DaySlot, ManagedReservation, Service } from "../../types.ts";
import { type Mock, fail } from "./context.ts";

export const DAYS_AHEAD = 7;
const MINUTES_PER_DAY = 24 * 60;
const NOT_FOUND = "No booking with that reference.";

/** The services by start, as the server stores them. */
export function services(mock: Mock): Service[] {
  return mock.data.hours.services.toSorted((a, b) => a.start.localeCompare(b.start));
}

/** Every bookable start of the day, by service. */
export function dayStarts(mock: Mock): string[] {
  return services(mock).flatMap(startsOf);
}

export function largestGroup(mock: Mock): number {
  const largestTable = Math.max(...mock.data.tables.map((table) => table.seats));
  return Math.min(mock.data.rules.max_party_size, largestTable);
}

const overlaps = (time: string, start: number, length: number) =>
  toMinutes(time) < start + length && start < toMinutes(time) + length;

/** The day's starts still to come, each with the table a party would get. */
export function slotsOf(mock: Mock, date: string, partySize: number, ignore?: string): DaySlot[] {
  const { data } = mock;
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
  return dayStarts(mock)
    .filter((time) => !mock.isPast(mock.instant(date, time)))
    .map((time) => {
      const start = toMinutes(time);
      const table = fitting.find(
        (candidate) =>
          !live.some((b) => b.table_label === candidate.label && overlaps(b.time, start, length)),
      );
      return {
        starts_at: mock.instant(date, time),
        time,
        until: toHHMM((start + length) % MINUTES_PER_DAY),
        table: table ?? null,
        blocked: blocked.has(time),
      };
    });
}

export function isFree(mock: Mock, date: string, time: string, label: string): boolean {
  const length = mock.data.rules.slot_length_min;
  const start = toMinutes(time);
  return !mock.data.reservations.some(
    (b) =>
      b.date === date &&
      b.status === "confirmed" &&
      b.table_label === label &&
      overlaps(b.time, start, length),
  );
}

export function find(mock: Mock, id: string): ManagedReservation {
  const booking = mock.data.reservations.find((b) => b.id === id.trim().toUpperCase());
  return booking ?? fail(mock, 404, "not_found", NOT_FOUND);
}

export function replace(mock: Mock, booking: ManagedReservation): void {
  mock.data = {
    ...mock.data,
    reservations: mock.data.reservations.map((b) => (b.id === booking.id ? booking : b)),
  };
}

/** A booking's activity line: "R-009, Sara Muller, 2 guests, today 18:30". */
export function summary(mock: Mock, booking: ManagedReservation): string {
  const day = dayWord(booking.date, mock.today);
  return `${booking.id}, ${booking.guest_name}, ${plural(booking.party_size, "guest")}, ${day} ${booking.time}`;
}

/** "today", "tomorrow", else "Tue 3 Nov", as the server words activity lines. */
export function dayWord(date: string, today: string): string {
  if (date === today) return "today";
  if (date === addDays(today, 1)) return "tomorrow";
  return dayShort(date);
}

/** The next reference; the live booking's stays free until it arrives. */
export function nextId(mock: Mock): string {
  const ids = [...mock.data.reservations.map((b) => b.id), mock.data.live_booking.reservation.id];
  const last = Math.max(0, ...ids.map((id) => Number(id.slice(2))));
  return `R-${String(last + 1).padStart(3, "0")}`;
}

/** The bookings of the next `DAYS_AHEAD` days, by start. */
export function upcoming(mock: Mock): ManagedReservation[] {
  const end = addDays(mock.today, DAYS_AHEAD - 1);
  return mock.data.reservations
    .filter((b) => b.date >= mock.today && b.date <= end)
    .toSorted((a, b) => `${a.date}${a.time}`.localeCompare(`${b.date}${b.time}`));
}

export function blockedAhead(mock: Mock): BlockedTime[] {
  return mock.data.blocked
    .filter((b) => !mock.isPast(b.starts_at))
    .toSorted((a, b) => a.starts_at.localeCompare(b.starts_at));
}

/** `guest_key` (agent/guests.py): runs of spaces are one, accents dropped, case folded. */
export function fold(name: string): string {
  return name.normalize("NFKD").replace(/\p{M}/gu, "").trim().split(/\s+/).join(" ").toLowerCase();
}
