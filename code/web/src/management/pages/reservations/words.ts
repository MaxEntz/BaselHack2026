// The Reservations page's wording (sesame-management.html#reservations, #139).
import type { DaySlot, ManagedReservation, Via } from "../../../types.ts";
import { addDays, dayLong, dayShort } from "../../dates.ts";
import { toHHMM, toMinutes } from "../../services.ts";
import { throughAgent } from "../home/format.ts";

export const guests = (n: number) => `${n} ${n === 1 ? "guest" : "guests"}`;
export const bookings = (n: number) => `${n} ${n === 1 ? "booking" : "bookings"}`;

const live = (booking: ManagedReservation) => booking.status === "confirmed";

/** "Tonight" (today's dinner), "Today", "Tomorrow", else the weekday: "Monday". */
export function dayLabel(day: string, today: string, serviceName: string): string {
  if (day === today) return serviceName.startsWith("Dinner") ? "Tonight" : "Today";
  if (day === addDays(today, 1)) return "Tomorrow";
  return dayLong(day).split(" ")[0];
}

/** "Tonight, 6 bookings and 17 guests." over the live bookings shown. */
export function summary(label: string, shown: readonly ManagedReservation[]): string {
  const on = shown.filter(live);
  return `${label}, ${bookings(on.length)} and ${guests(on.reduce((sum, b) => sum + b.party_size, 0))}.`;
}

/** A column's heading: "2 bookings, 5 guests" or "All cancelled", plus ", blocked". */
export function columnTitle(here: readonly ManagedReservation[], blocked: boolean): string {
  const on = here.filter(live);
  const count = on.length
    ? `${bookings(on.length)}, ${guests(on.reduce((sum, b) => sum + b.party_size, 0))}`
    : "All cancelled";
  return blocked ? `${count}, blocked` : count;
}

function channel(via: Via): string {
  if (throughAgent(via)) return "through your agent";
  return via === "website" ? "through your website" : "by you";
}

const capital = (text: string) => text[0].toUpperCase() + text.slice(1);

/** Under a guest's name: who booked it, or who cancelled it. */
export function rowNote(booking: ManagedReservation): string {
  if (!live(booking)) return `Cancelled by ${booking.cancelled_by === "guest" ? "the guest" : "you"}`;
  return capital(channel(booking.via));
}

/** "Saturday 31 October, 20:00 to 22:00" */
export function whenLine(booking: ManagedReservation, slotLengthMin: number): string {
  const end = toHHMM((toMinutes(booking.time) + slotLengthMin) % (24 * 60));
  return `${dayLong(booking.date)}, ${booking.time} to ${end}`;
}

/** "through your agent, Sat 31 Oct at 10:05": `created_at` is already the merchant's time. */
export function bookedLine(booking: ManagedReservation): string {
  return `${channel(booking.via)}, ${dayShort(booking.created_at.slice(0, 10))} at ${booking.created_at.slice(11, 16)}`;
}

/** Who gets told when the merchant cancels: a guest who booked through an agent or the website. */
export function guestBooked(booking: ManagedReservation): boolean {
  return booking.via !== "merchant";
}

/** The start nearest `time`, among `times`. */
export function nearest(times: readonly string[], time: string): string | undefined {
  return times.toSorted((a, b) => Math.abs(toMinutes(a) - toMinutes(time)) - Math.abs(toMinutes(b) - toMinutes(time)))[0];
}

/**
 * The new-booking dialog's live note, in the mockup's order, and whether it can be added.
 * `slots` are the day's starts still to come, for `party` (or 1 while `party` is invalid).
 */
export function newBookingNote({
  slots,
  time,
  party,
  limit,
}: {
  slots: readonly DaySlot[] | null;
  time: string | null;
  party: number;
  limit: number;
}): { text: string; ok: boolean } {
  if (slots !== null && !time) return { text: "Every time is blocked on this day.", ok: false };
  if (!(party >= 1) || !Number.isInteger(party)) return { text: "A booking needs at least one guest.", ok: false };
  if (party > limit) return { text: `Your limit is ${limit}. You can change it in the booking rules.`, ok: false };
  if (slots === null || time === null) return { text: "", ok: false };
  const slot = slots.find((s) => s.time === time);
  if (slot?.table) {
    return { text: `Table ${slot.table.label}, ${slot.table.seats} seats, until ${slot.until}.`, ok: true };
  }
  const free = slots.filter((s) => s.table && !s.blocked).map((s) => s.time);
  const other = nearest(free, time);
  return {
    text: other
      ? `No table for ${party} at ${time}. The closest free time is ${other}.`
      : `No table for ${party} is free on this day.`,
    ok: false,
  };
}
