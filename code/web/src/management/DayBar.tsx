import "./pages.css";
import type { Service } from "../types.ts";
import { dayLong } from "./dates.ts";
import { serviceNames } from "./services.ts";
import { Icon, Pill } from "./ui/index.ts";

/** One of `days` with round previous/next buttons, and a pill per service (Reservations, Floor plan). */
export function DayBar({
  days,
  day,
  onDay,
  services,
  service,
  onService,
}: {
  days: readonly string[];
  day: string;
  onDay: (day: string) => void;
  services: readonly Service[];
  service: number;
  onService: (index: number) => void;
}) {
  const index = days.indexOf(day);
  const names = serviceNames(services);
  return (
    <div className="bar-row">
      <div className="date">
        <button
          className="round"
          type="button"
          aria-label="Previous day"
          disabled={index <= 0}
          onClick={() => onDay(days[index - 1])}
        >
          <Icon name="left" />
        </button>
        <span className="num">{dayLong(day)}</span>
        <button
          className="round"
          type="button"
          aria-label="Next day"
          disabled={index >= days.length - 1}
          onClick={() => onDay(days[index + 1])}
        >
          <Icon name="right" />
        </button>
      </div>
      <div className="pills" role="group" aria-label="Service">
        {services.map(({ start, end }, i) => (
          <Pill small key={start} aria-pressed={i === service} onClick={() => onService(i)}>
            {names[i]}, {start}–{end}
          </Pill>
        ))}
      </div>
    </div>
  );
}
