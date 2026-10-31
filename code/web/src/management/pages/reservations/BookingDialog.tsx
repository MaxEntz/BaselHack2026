import { type FormEvent, useCallback, useEffect, useState } from "react";
import type { DaySlot, ManagedReservation, MerchantCancelOutput } from "../../../types.ts";
import { useClient } from "../../data.ts";
import { errorMessage } from "../../problems.ts";
import { Dialog, Pill, useToast } from "../../ui/index.ts";
import { bookedLine, guestBooked, guests, nearest, whenLine } from "./words.ts";

/**
 * One booking: its facts, and for a live one, a move to another free time of the day and a
 * cancellation, asked twice. `started` hides the move: the server refuses to move a booking
 * once it has started.
 */
export function BookingDialog({
  slug,
  booking,
  slotLengthMin,
  started,
  onClose,
  onMoved,
  onCancelled,
}: {
  slug: string;
  booking: ManagedReservation | null;
  slotLengthMin: number;
  started: boolean;
  onClose: () => void;
  onMoved: (booking: ManagedReservation) => void;
  onCancelled: (answer: MerchantCancelOutput) => void;
}) {
  return (
    <Dialog open={booking !== null} onClose={onClose} title={booking?.guest_name ?? ""}>
      {booking && (
        <BookingBody
          key={booking.id}
          slug={slug}
          booking={booking}
          slotLengthMin={slotLengthMin}
          started={started}
          onMoved={onMoved}
          onCancelled={onCancelled}
        />
      )}
    </Dialog>
  );
}

function BookingBody({
  slug,
  booking,
  slotLengthMin,
  started,
  onMoved,
  onCancelled,
}: {
  slug: string;
  booking: ManagedReservation;
  slotLengthMin: number;
  started: boolean;
  onMoved: (booking: ManagedReservation) => void;
  onCancelled: (answer: MerchantCancelOutput) => void;
}) {
  const client = useClient();
  const toast = useToast();
  const live = booking.status === "confirmed";
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The other free starts of the day, with their instant as the server words it.
  const [free, setFree] = useState<DaySlot[] | null>(null);
  const times = free?.map((slot) => slot.time) ?? null;
  const [chosen, setChosen] = useState<string | null>(null);

  const loadTimes = useCallback(
    async (signal: AbortSignal) => {
      const { slots } = await client.slots(
        slug,
        { date: booking.date, partySize: booking.party_size, ignore: booking.id },
        signal,
      );
      return slots.filter((s) => s.table && !s.blocked && s.time !== booking.time);
    },
    [client, slug, booking],
  );
  useEffect(() => {
    if (!live || started) return;
    const controller = new AbortController();
    loadTimes(controller.signal).then(
      (others) => {
        if (controller.signal.aborted) return;
        setFree(others);
        setChosen(nearest(others.map((slot) => slot.time), booking.time) ?? null);
      },
      (failure: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      },
    );
    return () => controller.abort();
  }, [live, started, loadTimes, booking.time]);

  const facts: [string, string][] = [
    ["When", whenLine(booking, slotLengthMin)],
    ["Party", guests(booking.party_size)],
    ["Table", booking.table_label],
    ["Booked", bookedLine(booking)],
    ["Reference", booking.id],
  ];
  if (!live) facts.push(["Status", `Cancelled by ${booking.cancelled_by === "guest" ? "the guest" : "you"}`]);

  async function move(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const slot = free?.find((s) => s.time === chosen);
    if (!slot || busy) return;
    setBusy(true);
    setError(null);
    try {
      const moved = await client.moveBooking(slug, booking.id, {
        starts_at: slot.starts_at,
        idempotency_key: crypto.randomUUID(),
      });
      toast(
        <>
          <b>{moved.guest_name} moved</b>
          {moved.time}, table {moved.table_label}
        </>,
      );
      onMoved(moved);
    } catch (failure) {
      setError(errorMessage(failure));
      setBusy(false);
    }
  }

  async function cancel() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const answer = await client.cancelBooking(slug, booking.id);
      const { reservation: r } = answer;
      toast(
        <>
          <b>{r.guest_name} is cancelled</b>
          Table {r.table_label} is free again.
          {answer.outcome === "notified" && " The guest sees a notice next time their assistant checks."}
        </>,
      );
      onCancelled(answer);
    } catch (failure) {
      setError(errorMessage(failure));
      setBusy(false);
    }
  }

  return (
    <>
      <dl className="facts">
        {facts.map(([term, value]) => (
          <div key={term}>
            <dt>{term}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {live && !started && (
        <form onSubmit={move}>
          <label className="field">
            Move to another time
            <span className="move">
              <select
                aria-label="Move to another time"
                disabled={!times?.length}
                value={times?.length ? (chosen ?? "") : ""}
                onChange={(event) => setChosen(event.target.value)}
              >
                {times?.length ? (
                  times.map((time) => <option key={time}>{time}</option>)
                ) : (
                  <option value="">{times ? "No other time is free" : ""}</option>
                )}
              </select>
              <Pill small type="submit" disabled={!times?.length || busy}>
                Move
              </Pill>
            </span>
          </label>
        </form>
      )}
      {error && (
        <p className="note bad" role="alert">
          {error}
        </p>
      )}
      {live &&
        (confirming ? (
          <>
            <p className="note">
              <b>Cancel this booking?</b>{" "}
              {guestBooked(booking)
                ? `${booking.guest_name} sees a notice the next time their assistant checks.`
                : "The table goes back on offer straight away."}
            </p>
            <div className="dlg-foot">
              <Pill small onClick={() => setConfirming(false)}>
                Keep it
              </Pill>
              <Pill small primary disabled={busy} onClick={cancel}>
                Cancel the booking
              </Pill>
            </div>
          </>
        ) : (
          <div className="dlg-foot">
            <Pill small onClick={() => setConfirming(true)}>
              Cancel the booking
            </Pill>
          </div>
        ))}
    </>
  );
}
