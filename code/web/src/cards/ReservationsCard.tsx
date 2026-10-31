import type { AdminReservationRow } from "../types.ts";
import { VIA_LABELS, describeDate, plural } from "./format.ts";

type Props = { data: { date: string; reservations: AdminReservationRow[] } };

export function ReservationsCard({ data }: Props) {
  const when = describeDate(data.date);
  const rows = data.reservations.toSorted((a, b) => a.time.localeCompare(b.time));
  const confirmed = rows.filter((row) => row.status === "confirmed");
  const guests = confirmed.reduce((sum, row) => sum + row.party_size, 0);

  if (rows.length === 0) {
    return (
      <section className="card" aria-label="Bookings">
        <h2 className="card-title">Bookings {when}</h2>
        <p className="empty">No bookings {when} yet.</p>
      </section>
    );
  }

  return (
    <section className="card" aria-label="Bookings">
      <h2 className="card-title">Bookings {when}</h2>
      <div className="table-scroll">
        <table className="reservations">
          <thead>
            <tr>
              <th>Time</th>
              <th>Ref</th>
              <th>Guest</th>
              <th className="numeric">Party</th>
              <th>Table</th>
              <th>Origin</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className={row.status === "cancelled" ? "is-cancelled" : undefined}>
                <td className="strong">{row.time}</td>
                <td>
                  <code>{row.id}</code>
                </td>
                <td>
                  {row.guest_name}
                  {row.status === "cancelled" && <span className="muted"> · cancelled</span>}
                </td>
                <td className="numeric">{row.party_size}</td>
                <td>{row.table_label}</td>
                <td>
                  <span className={`badge badge-${row.via}`}>{VIA_LABELS[row.via]}</span>
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={6}>
                {plural(confirmed.length, "booking")} · {plural(guests, "guest")}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
