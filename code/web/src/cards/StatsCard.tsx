import type { Stats } from "../types.ts";
import { describeDate } from "./format.ts";

export function StatsCard({ data }: { data: Stats & { date: string } }) {
  const occupancy =
    data.seats > 0 ? Math.round((data.guests / data.seats) * 100) : null;
  const figures = [
    { label: "Bookings", value: data.bookings },
    { label: "Guests", value: data.guests },
    { label: "Seats", value: data.seats },
    ...(occupancy === null ? [] : [{ label: "Occupancy", value: `${occupancy}%` }]),
  ];

  return (
    <section className="card" aria-label="Statistics">
      <h2 className="card-title">Your numbers {describeDate(data.date)}</h2>
      <dl className="figures">
        {figures.map(({ label, value }) => (
          <div key={label} className="figure">
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {data.blocked.length > 0 && (
        <ul className="chips" aria-label="Blocked slots">
          {data.blocked.map((slot) => (
            <li key={slot} className="chip chip-blocked">
              {slot} blocked
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
