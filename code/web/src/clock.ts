// The time pages read. Tests fix it (`setClock(DEMO_NOW)`) instead of mocking Date globally;
// Playwright can also use `page.clock`, since `now()` reads Date.now().
import { useEffect, useState } from "react";

/** The demo's "now": Saturday 31 October 2026, 19:40 in Zurich. */
export const DEMO_NOW = new Date("2026-10-31T19:40:00+01:00");

let fixed: number | null = null;

export function now(): Date {
  return new Date(fixed ?? Date.now());
}

/** Fix the clock at `at`, or let it run again with `null`. */
export function setClock(at: Date | null): void {
  fixed = at?.getTime() ?? null;
}

/** The current time, re-read every `everyMs` (default: one minute). */
export function useNow(everyMs = 60_000): Date {
  const [value, setValue] = useState(now);
  useEffect(() => {
    const timer = setInterval(() => setValue(now()), everyMs);
    return () => clearInterval(timer);
  }, [everyMs]);
  return value;
}
