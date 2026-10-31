import type { Service, Via } from "../types.ts";

export const VIA_LABELS: Record<Via, string> = {
  claude: "Claude",
  website: "Website",
  merchant: "Merchant",
};

export function formatDuration(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function formatServices(services: readonly Service[]): string {
  return services.map(({ start, end }) => `${start}–${end}`).join(" · ");
}

export function formatMinutes(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest} min`;
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

export function plural(count: number, singular: string, pluralForm = `${singular}s`) {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

// "tonight" for today's date, "on Sat 1 Nov" otherwise.
export function describeDate(date: string): string {
  const day = new Date(`${date}T12:00:00`);
  if (Number.isNaN(day.getTime())) return `on ${date}`;
  if (day.toDateString() === new Date().toDateString()) return "tonight";
  const label = day.toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
  return `on ${label}`;
}
