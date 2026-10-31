// Home's wording (sesame-management.html#home). Days are the merchant's local
// YYYY-MM-DD, worded in English like the mockup.

import type { ActivityKind, ChannelCount, Service, Via } from "../../../types.ts";

const dayFormat = (date: string, options: Intl.DateTimeFormatOptions) =>
  new Date(`${date}T12:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", ...options });

/** "Saturday 31 October" */
export const dayLong = (date: string) =>
  dayFormat(date, { weekday: "long", day: "numeric", month: "long" }).replace(",", "");
/** "Sat 31 Oct" */
export const dayShort = (date: string) =>
  dayFormat(date, { weekday: "short", day: "numeric", month: "short" }).replace(",", "");
/** "Sat" */
export const weekday = (date: string) => dayFormat(date, { weekday: "short" });
/** "31 Oct" */
export const dayMonth = (date: string) => dayFormat(date, { day: "numeric", month: "short" });

/** 982 → "982", 1204 → "1,204" */
export const count = (n: number) => n.toLocaleString("en-GB");
export const guests = (n: number) => `${count(n)} ${n === 1 ? "guest" : "guests"}`;
export const bookings = (n: number) => `${count(n)} ${n === 1 ? "booking" : "bookings"}`;

/** "Good morning" before noon, "Good afternoon" before 18:00, else "Good evening". */
export function greeting(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/** The hour (0-23) of `at` in `timeZone`. */
export function hourIn(at: Date, timeZone: string): number {
  const hour = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", hourCycle: "h23", timeZone })
    .formatToParts(at)
    .find((part) => part.type === "hour")?.value;
  return Number(hour ?? at.getHours());
}

/** "Léa Dubois" → "Léa"; null or blank → null. */
export function firstName(ownerName: string | null): string | null {
  const first = ownerName?.trim().split(/\s+/)[0];
  return first ? first : null;
}

/** A service that starts before 16:00 is lunch, any later one dinner. */
export function serviceWord(service: Service): "lunch" | "dinner" {
  return Number(service.start.slice(0, 2)) < 16 ? "lunch" : "dinner";
}

/** "Saturday 31 October, dinner 18:30–21:30" */
export function subtitle(date: string, service: Service): string {
  return `${dayLong(date)}, ${serviceWord(service)} ${service.start}–${service.end}`;
}

/** "Through an agent": any channel but the merchant itself and the website. */
export function throughAgent(via: Via): boolean {
  return via !== "merchant" && via !== "website";
}

export const KIND: Record<ActivityKind, string> = {
  new_booking: "New booking",
  moved: "Moved",
  cancelled: "Cancelled",
  blocked: "Blocked",
  reopened: "Reopened",
  saved: "Saved",
  paused: "Paused",
  resumed: "Resumed",
};

/** Bookings through an agent over all bookings, in whole percent; 0 with no booking. */
export function agentShare(channels: readonly ChannelCount[]): { share: number; total: number } {
  const total = channels.reduce((sum, c) => sum + c.bookings, 0);
  const agent = channels.filter((c) => throughAgent(c.via)).reduce((sum, c) => sum + c.bookings, 0);
  return { share: total === 0 ? 0 : Math.round((agent / total) * 100), total };
}

/** HH:MM of an ISO instant already written in the merchant's offset. */
export const clockOf = (instant: string) => instant.slice(11, 16);

/** Minutes since midnight of HH:MM. */
export const minutes = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5));
