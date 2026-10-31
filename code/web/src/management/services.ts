import type { Service } from "../types.ts";

// AGENTS.md §6: a start every 30 minutes, the last one an hour before the end.
const STEP_MIN = 30;
const LAST_SEATING_MIN = 60;

export function toMinutes(hhmm: string): number {
  const [hours, minutes] = hhmm.split(":").map(Number);
  return hours * 60 + minutes;
}

export function toHHMM(minutes: number): string {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

/** The last start of a service, or null when it is too short for one (or half filled in). */
export function lastSeating({ start, end }: Service): string | null {
  if (!start || !end) return null;
  const last = toMinutes(end) - LAST_SEATING_MIN;
  return last >= toMinutes(start) ? toHHMM(last) : null;
}

/** Every bookable start of a service. */
export function startsOf(service: Service): string[] {
  const last = lastSeating(service);
  if (last === null) return [];
  const starts = [];
  for (let at = toMinutes(service.start); at <= toMinutes(last); at += STEP_MIN) starts.push(toHHMM(at));
  return starts;
}

/** "Breakfast", "Lunch" or "Dinner" by start time; a second one of a kind is "Lunch 2". */
export function serviceNames(services: readonly Service[]): string[] {
  const seen = new Map<string, number>();
  return services.map(({ start }) => {
    const at = toMinutes(start);
    const name = at < toMinutes("11:00") ? "Breakfast" : at < toMinutes("16:00") ? "Lunch" : "Dinner";
    const count = (seen.get(name) ?? 0) + 1;
    seen.set(name, count);
    return count === 1 ? name : `${name} ${count}`;
  });
}

/** The service a page opens on: today, the current or next one, else the last; another day, the last. */
export function defaultService(services: readonly Service[], today: boolean, clock: string): number {
  const current = today ? services.findIndex((service) => clock < service.end) : -1;
  return current >= 0 ? current : services.length - 1;
}

/** The index of the service `time` falls in (start included, end excluded), or -1. */
export function serviceOf(services: readonly Service[], time: string): number {
  return services.findIndex(({ start, end }) => start <= time && time < end);
}
