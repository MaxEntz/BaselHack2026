// A merchant's local day (YYYY-MM-DD) in words. The day is already local: format it in UTC so
// that the browser's own zone can't shift it.

function format(date: string, options: Intl.DateTimeFormatOptions): string {
  const [year, month, day] = date.split("-").map(Number);
  return new Intl.DateTimeFormat("en-GB", { ...options, timeZone: "UTC" })
    .format(new Date(Date.UTC(year, month - 1, day)))
    .replace(",", "");
}

/** "Sun 1 Nov" */
export function dayShort(date: string): string {
  return format(date, { weekday: "short", day: "numeric", month: "short" });
}

/** "Saturday 31 October" */
export function dayLong(date: string): string {
  return format(date, { weekday: "long", day: "numeric", month: "long" });
}
