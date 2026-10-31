import type { ManagedReservation, Service } from "../types.ts";

// Until the owner publishes, Home shows these instead of bookings (#136): the web's own
// fixture, never the merchant's tables, so publishing has nothing to clear.
const GUESTS: readonly [name: string, party: number, via: ManagedReservation["via"]][] = [
  ["Alex Martin", 2, "website"],
  ["Sam Dupont", 4, "claude"],
  ["Nora Haas", 3, "claude"],
];

export const SAMPLE_PREFIX = "sample-";

/** Three sample bookings an hour apart from the service's start, on `date`. */
export function sampleBookings(date: string, service: Service): ManagedReservation[] {
  const [hours, mins] = service.start.split(":").map(Number);
  return GUESTS.map(([guest_name, party_size, via], index) => ({
    id: `${SAMPLE_PREFIX}${index + 1}`,
    date,
    time: `${String(hours + index).padStart(2, "0")}:${String(mins).padStart(2, "0")}`,
    guest_name,
    party_size,
    table_label: `T${index + 1}`,
    via,
    status: "confirmed",
    cancelled_by: null,
    created_at: `${date}T09:00:00Z`,
  }));
}
