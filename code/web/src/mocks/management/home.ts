// Home's route (#135): the day's numbers, the room and the latest activity.
import type { ManagementClient } from "../../management/client.ts";
import { addDays } from "../../management/dates.ts";
import { throughAgent } from "../../management/pages/home/format.ts";
import { toMinutes } from "../../management/services.ts";
import type { Dashboard, ManagedReservation, Room, Via } from "../../types.ts";
import { DAYS_AHEAD, largestGroup, services, slotsOf } from "./bookings.ts";
import { type Mock, liveByVia, local, respond } from "./context.ts";

const ROOM_PARTY = 4;
const ACTIVITY_LIMIT = 6;

export function homeRoutes(mock: Mock): Pick<ManagementClient, "dashboard"> {
  return { dashboard: async () => respond(mock, dashboard(mock)) };
}

function dashboard(mock: Mock): Dashboard {
  const { data, today } = mock;
  const current = liveByVia(data.reservations, today);
  const vias = new Set<Via>([...data.channels.map((c) => c.via), ...current.keys()]);
  // The fixture's counts, with the changes made to today since the mock started.
  const channels = [...vias].map((via) => ({
    via,
    bookings:
      (data.channels.find((c) => c.via === via)?.bookings ?? 0) +
      (current.get(via) ?? 0) -
      (mock.baseline.get(via) ?? 0),
  }));
  const day = (d: string) => data.reservations.filter((b) => b.date === d);
  const live = (d: string) => day(d).filter((b) => b.status === "confirmed");
  const sum = (bookings: ManagedReservation[]) => bookings.reduce((total, b) => total + b.party_size, 0);
  const history = data.history.map((entry) =>
    entry.date === today
      ? {
          date: today,
          bookings: live(today).length,
          guests: sum(live(today)),
          agent_guests: sum(live(today).filter((b) => throughAgent(b.via))),
          cancelled: day(today).length - live(today).length,
        }
      : entry,
  );
  const nextDays = Array.from({ length: DAYS_AHEAD }, (_, index) => {
    const d = addDays(today, index);
    return { date: d, guests: sum(live(d)) };
  });
  return {
    date: today,
    history,
    next_days: nextDays,
    channels,
    room: room(mock),
    activity: data.activity.slice(0, ACTIVITY_LIMIT),
  };
}

// The service under way or next, its booked tables, and the next free start for a usual party.
function room(mock: Mock): Room {
  const { data } = mock;
  const clock = local(mock.now(), data.merchant.timezone);
  const sorted = services(mock);
  const service =
    sorted.find((s) => toMinutes(s.end) > toMinutes(clock.time)) ?? sorted[sorted.length - 1];
  const within = (time: string) =>
    toMinutes(time) >= toMinutes(service.start) && toMinutes(time) < toMinutes(service.end);
  const live = data.reservations.filter(
    (b) => b.date === clock.date && b.status === "confirmed" && within(b.time),
  );
  const booked = new Set(live.map((b) => b.table_label));
  const largest = largestGroup(mock);
  const partySize = Math.min(ROOM_PARTY, largest);
  const next = slotsOf(mock, clock.date, partySize).find(
    (s) => !s.blocked && s.table && within(s.time),
  );
  return {
    service,
    seats: data.tables.reduce((sum, table) => sum + table.seats, 0),
    guests: live.reduce((sum, b) => sum + b.party_size, 0),
    tables: data.tables.map((table) => ({ ...table, booked: booked.has(table.label) })),
    party_size: partySize,
    next_free: next?.time ?? null,
    largest_group: largest,
  };
}
