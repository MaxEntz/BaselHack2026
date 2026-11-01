// The four moments of a restaurant's day (#154): which one a time falls in, and the time each one
// is previewed at. Pure, for Home and for Studio.
import { toHHMM, toMinutes } from "./management/services.ts";
import type { Moment, Service } from "./types.ts";

export const MOMENTS: readonly { id: Moment; label: string }[] = [
  { id: "morning", label: "Morning" },
  { id: "before", label: "Before service" },
  { id: "during", label: "During service" },
  { id: "after", label: "After service" },
];

// The list matters this long before a service; earlier, it's still the day ahead.
const BEFORE_WINDOW_MIN = 60;
// An hour in: some guests are seated, and the type this moment is for still has an arrival to show.
const DURING_PREVIEW_MIN = 60;
const AFTER_PREVIEW_MIN = 60;
const LAST_MINUTE = 23 * 60 + 59;

export const isMoment = (value: unknown): value is Moment =>
  MOMENTS.some((moment) => moment.id === value);

/** What Studio's dock posts to the management site in its frame. */
export type MomentMessage = { type: "sesame:moment"; moment: Moment | null };

/**
 * What the management site in Studio's frame posts once it listens for `MomentMessage`s. It loads
 * on its first visit (#220), after the frame's `load`: a moment posted before would be lost.
 */
export const MOMENT_READY = { type: "sesame:moment-ready" } as const;

export const isMomentReady = (data: unknown): boolean =>
  typeof data === "object" && data !== null && "type" in data && data.type === MOMENT_READY.type;

/** The moment a `MomentMessage` asks for, null for the clock's; undefined for any other message. */
export function askedMoment(data: unknown): Moment | null | undefined {
  if (typeof data !== "object" || data === null) return undefined;
  if (!("type" in data) || data.type !== "sesame:moment" || !("moment" in data)) return undefined;
  const { moment } = data;
  return moment === null || isMoment(moment) ? moment : undefined;
}


const inOrder = (services: readonly Service[]) =>
  services.toSorted((a, b) => toMinutes(a.start) - toMinutes(b.start));

export type MomentAt = { moment: Moment; service: Service; time: string };

/** The moment `time` falls in, and the service it is about; null without a service. */
export function momentAt(time: string, services: readonly Service[]): MomentAt | null {
  const sorted = inOrder(services);
  const last = sorted.at(-1);
  if (!last) return null;
  const at = toMinutes(time);
  for (const service of sorted) {
    if (at >= toMinutes(service.start) && at < toMinutes(service.end)) {
      return { moment: "during", service, time };
    }
    if (at >= toMinutes(service.start) - BEFORE_WINDOW_MIN && at < toMinutes(service.start)) {
      return { moment: "before", service, time };
    }
  }
  if (at >= toMinutes(last.end)) return { moment: "after", service: last, time };
  return { moment: "morning", service: last, time };
}

/** The time a moment is previewed at, on the day's last service. */
export function previewTime(moment: Moment, services: readonly Service[]): string | null {
  const last = inOrder(services).at(-1);
  if (!last) return null;
  switch (moment) {
    case "morning":
      return "10:00";
    case "before":
      return toHHMM(toMinutes(last.start) - BEFORE_WINDOW_MIN);
    case "during":
      return toHHMM(toMinutes(last.start) + DURING_PREVIEW_MIN);
    case "after":
      return toHHMM(Math.min(toMinutes(last.end) + AFTER_PREVIEW_MIN, LAST_MINUTE));
  }
}

/** The moment to show: the one asked for, at its preview time, or the clock's. */
export function shownMoment(
  previewed: Moment | null,
  time: string,
  services: readonly Service[],
): MomentAt | null {
  const last = inOrder(services).at(-1);
  if (!previewed || !last) return momentAt(time, services);
  return { moment: previewed, service: last, time: previewTime(previewed, services) ?? time };
}

const SHOWS: Record<Moment, string> = {
  morning: "the site shows the day ahead and what to check.",
  before: "it shows tonight's list and which tables are taken.",
  during: "it shows the next arrival in type you can read from across the room.",
  after: "it shows how the service went.",
};

/** What Studio says when it previews a moment. */
export function momentSentence(moment: Moment, services: readonly Service[]): string {
  const time = previewTime(moment, services);
  const sentence = SHOWS[moment];
  return time
    ? `At ${time} ${sentence}`
    : `${sentence.charAt(0).toUpperCase()}${sentence.slice(1)}`;
}
