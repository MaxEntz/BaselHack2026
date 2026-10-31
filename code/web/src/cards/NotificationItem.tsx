import type { LiveEvent } from "../types.ts";
import { VIA_LABELS, describeDay, notificationTitle, plural } from "./format.ts";

export function NotificationItem({ event }: { event: LiveEvent }) {
  const { kind, via, date, reservation } = event;
  return (
    <div className={`notification notification-${kind}`} role="alert">
      <span className="notification-icon" aria-hidden="true">
        🔔
      </span>
      <div>
        <strong>{notificationTitle(event)}</strong>
        <p>
          {reservation.id} · {plural(reservation.party_size, "guest")} · {describeDay(date)}{" "}
          {reservation.time} · table {reservation.table_label}
          {kind !== "new_booking" && ` · booked via ${VIA_LABELS[via]}`}
        </p>
      </div>
    </div>
  );
}
