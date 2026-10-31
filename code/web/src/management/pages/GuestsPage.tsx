import { useCallback, useState } from "react";
import { Link } from "react-router";
import { useClient, useLoad } from "../data.ts";
import { dayShort } from "../dates.ts";
import { PAGES, bookingPath } from "../pages.ts";
import { Source } from "../ui/index.ts";
import { PageTitle } from "./PageTitle.tsx";

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
                  {data.guests.map((guest) => (
                    <tr key={guest.reservation_id}>
                      <td>
                        <Link className="guest" to={bookingPath(slug, guest.date, guest.reservation_id)}>
                          {guest.guest_name}
                        </Link>
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
          ) : (
            <p className="none">Nobody by that name.</p>
          ))}
      </div>
    </>
  );
}
