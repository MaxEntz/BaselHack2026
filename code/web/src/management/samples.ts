import type { ManagedReservation, Service } from "../types.ts";
import { startsOf } from "./services.ts";

// Until the owner publishes, Home shows these instead of bookings (#136): the web's own
// fixture, never the merchant's tables, so publishing has nothing to clear.
const GUESTS: readonly [name: string, party: number, via: ManagedReservation["via"]][] = [
  ["Alex Martin", 2, "website"],
  ["Sam Dupont", 4, "claude"],
  ["Nora Haas", 3, "claude"],
];

export const SAMPLE_PREFIX = "sample-";

/** Three sample bookings on `date`, at the service's first, middle and last start (#184). */
export function sampleBookings(date: string, service: Service): ManagedReservation[] {
  const starts = startsOf(service);
  if (starts.length === 0) return [];
  const times = [starts[0], starts[Math.floor((starts.length - 1) / 2)], starts[starts.length - 1]];
  return GUESTS.map(([guest_name, party_size, via], index) => ({
    id: `${SAMPLE_PREFIX}${index + 1}`,
    date,
    time: times[index],
    guest_name,
    party_size,
    table_label: `T${index + 1}`,
    via,
    status: "confirmed",
    cancelled_by: null,
    created_at: `${date}T09:00:00Z`,
  }));
}
