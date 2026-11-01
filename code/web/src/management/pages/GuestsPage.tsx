import { useCallback, useState } from "react";
import type { GuestRow, ManagedReservation } from "../../types.ts";
import { useClient, useLoad } from "../data.ts";
import { dayShort } from "../dates.ts";
import { PAGES } from "../pages.ts";
import { errorMessage } from "../problems.ts";
import { Source, useToast } from "../ui/index.ts";
import { PageTitle } from "./PageTitle.tsx";
import { BookingDialog } from "./reservations/BookingDialog.tsx";
import { hasEnded, hasStarted } from "./reservations/words.ts";

type Opened = { booking: ManagedReservation; slotLengthMin: number; started: boolean; ended: boolean };

/**
 * Everyone with a booking ahead in the next 7 days, one row per guest (#144). Read again on each
 * live event (`version`): a booking through an agent may bring a new guest.
 */
export function GuestsPage({ slug, version }: { slug: string; version: number }) {
  const client = useClient();
  const [query, setQuery] = useState("");
  const load = useCallback(
    (signal: AbortSignal) => client.guests(slug, query, signal),
    [client, slug, query],
  );
  const { data, error } = useLoad(load, version);
  const toast = useToast();
  const [opened, setOpened] = useState<Opened | null>(null);

  // The booking's day gives its slot length; the merchant's clock, read now, whether it has started.
  async function open(guest: GuestRow) {
    try {
      const [{ now }, day] = await Promise.all([client.merchant(slug), client.reservations(slug, guest.date)]);
      const booking = day.reservations.find((b) => b.id === guest.reservation_id);
      if (!booking) return;
      const [today, clock] = [now.slice(0, 10), now.slice(11, 16)];
      setOpened({
        booking,
        slotLengthMin: day.slot_length_min,
        started: hasStarted(booking, today, clock),
        ended: hasEnded(booking, today, clock, day.slot_length_min),
      });
    } catch (failure) {
      toast(errorMessage(failure));
    }
  }

  return (
    <>
      <PageTitle title={PAGES.guests}>
        Everyone booked in the next 7 days. Sésame keeps a name for each booking and no accounts.
      </PageTitle>
      <div className="fs tight">
        <label className="field">
          <span className="sr">Find a guest</span>
          <input
            type="text"
            placeholder="Find a guest"
            autoComplete="off"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        {error && (
          <p className="err" role="alert">
            {error}
          </p>
        )}
        {data &&
          (data.guests.length ? (
            <GuestsTable guests={data.guests} onOpen={(guest) => void open(guest)} />
          ) : (
            <p className="none">Nobody by that name.</p>
          ))}
      </div>
      <BookingDialog
        slug={slug}
        booking={opened?.booking ?? null}
        slotLengthMin={opened?.slotLengthMin ?? 0}
        started={opened?.started ?? false}
        ended={opened?.ended ?? false}
        onClose={() => setOpened(null)}
        onMoved={() => setOpened(null)}
        onCancelled={() => setOpened(null)}
      />
    </>
  );
}

function GuestsTable({ guests, onOpen }: { guests: readonly GuestRow[]; onOpen: (guest: GuestRow) => void }) {
  return (
    <div className="tbl-wrap">
      <table className="tbl">
        <thead>
          <tr>
            <th>Guest</th>
            <th>Next visit</th>
            <th>Party</th>
            <th>Bookings</th>
            <th>Booked by</th>
          </tr>
        </thead>
        <tbody>
          {guests.map((guest) => (
            <tr key={guest.reservation_id}>
              <td>
                <button type="button" onClick={() => onOpen(guest)}>
                  {guest.guest_name}
                </button>
              </td>
              <td>
                {dayShort(guest.date)}, {guest.time}
              </td>
              <td>{guest.party_size}</td>
              <td>{guest.bookings}</td>
              <td>
                <Source via={guest.via} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
