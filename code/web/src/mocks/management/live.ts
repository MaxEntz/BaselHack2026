// Live events (GET /events): the demo's one booking by an agent, a few seconds after the site
// opens.
import type { ManagementClient } from "../../management/client.ts";
import type { ManagedReservation } from "../../types.ts";
import { isFree, slotsOf, summary } from "./bookings.ts";
import { type Mock, log } from "./context.ts";

export function liveRoutes(mock: Mock): Pick<ManagementClient, "subscribe"> {
  return {
    subscribe(_slug, handlers) {
      mock.listeners.add(handlers);
      const connected = setTimeout(() => handlers.onConnection(true), mock.latencyMs);
      if (!mock.delivered && mock.liveTimer === undefined) {
        mock.liveTimer = setTimeout(() => deliverLiveBooking(mock), mock.liveBookingDelayMs);
      }
      return () => {
        clearTimeout(connected);
        mock.listeners.delete(handlers);
        if (mock.listeners.size === 0 && !mock.delivered) {
          clearTimeout(mock.liveTimer);
          mock.liveTimer = undefined;
        }
      };
    },
  };
}

// The agent's booking, once for every listener: it keeps the fixture's table, unless the
// merchant took it meanwhile.
function deliverLiveBooking(mock: Mock): void {
  mock.delivered = true;
  const event = mock.data.live_booking;
  const { reservation } = event;
  const slot = slotsOf(mock, event.date, reservation.party_size).find(
    (s) => s.time === reservation.time,
  );
  if (!slot?.table || slot.blocked) return;
  const own = mock.data.tables.find((table) => table.label === reservation.table_label);
  const table = own && isFree(mock, event.date, reservation.time, own.label) ? own : slot.table;
  const booked = { ...event, reservation: { ...reservation, table_label: table.label } };
  const booking: ManagedReservation = {
    ...booked.reservation,
    date: booked.date,
    cancelled_by: null,
    created_at: mock.nowIso(),
  };
  mock.data = { ...mock.data, reservations: [...mock.data.reservations, booking] };
  log(mock, "new_booking", summary(mock, booking), booked.via, booking.id);
  for (const listener of mock.listeners) listener.onEvent(structuredClone(booked));
}
