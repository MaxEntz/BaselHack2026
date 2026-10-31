import type { LiveEvent } from "../types.ts";
import { VIA_LABELS, describeDay, plural } from "./format.ts";

const TITLES: Record<LiveEvent["kind"], string> = {
  new_booking: "New booking",
  updated: "Booking updated",
  cancelled: "Booking cancelled",
};

export function NotificationItem({ event }: { event: LiveEvent }) {
  const { kind, via, date, reservation } = event;
  return (
    <div className={`notification notification-${kind}`} role="alert">
      <span className="notification-icon" aria-hidden="true">
        🔔
      </span>
      <div>
        <strong>
          {TITLES[kind]} via {VIA_LABELS[via]}
        </strong>
        <p>
          {reservation.id} · {plural(reservation.party_size, "guest")} · {describeDay(date)}{" "}
          {reservation.time} · table {reservation.table_label}
        </p>
      </div>
    </div>
  );
}
