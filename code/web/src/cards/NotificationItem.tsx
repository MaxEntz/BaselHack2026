import type { LiveEvent } from "../types.ts";
import { VIA_LABELS, describeDay, plural } from "./format.ts";

// Live events only report guests' changes: the merchant's own are not sent.
function title({ kind, via }: LiveEvent): string {
  switch (kind) {
    case "new_booking":
      return `New booking via ${VIA_LABELS[via]}`;
    case "updated":
      return "Booking updated by the guest";
    case "cancelled":
      return "Booking cancelled by the guest";
  }
}

export function NotificationItem({ event }: { event: LiveEvent }) {
  const { kind, via, date, reservation } = event;
  return (
    <div className={`notification notification-${kind}`} role="alert">
      <span className="notification-icon" aria-hidden="true">
        🔔
      </span>
      <div>
        <strong>{title(event)}</strong>
        <p>
          {reservation.id} · {plural(reservation.party_size, "guest")} · {describeDay(date)}{" "}
          {reservation.time} · table {reservation.table_label}
          {kind !== "new_booking" && ` · booked via ${VIA_LABELS[via]}`}
        </p>
      </div>
    </div>
  );
}
