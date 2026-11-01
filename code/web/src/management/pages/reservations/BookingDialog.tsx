import { type FormEvent, useCallback, useEffect, useState } from "react";
import type { DaySlot, ManagedReservation, MerchantCancelOutput } from "../../../types.ts";
import { useClient } from "../../data.ts";
import { useIdempotencyKey } from "../../keys.ts";
import { errorMessage } from "../../problems.ts";
import { Dialog, Pill, useToast } from "../../ui/index.ts";
import { bookedLine, cancelNote, guests, nearest, whenLine } from "./words.ts";

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
  ended,
  onClose,
  onMoved,
  onCancelled,
}: {
  slug: string;
  booking: ManagedReservation | null;
  slotLengthMin: number;
  started: boolean;
  ended: boolean;
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
          ended={ended}
          onMoved={onMoved}
          onCancelled={onCancelled}
        />
      )}
    </Dialog>
  );
}

type BookingBodyProps = {
  slug: string;
  booking: ManagedReservation;
  slotLengthMin: number;
  started: boolean;
  ended: boolean;
  onMoved: (booking: ManagedReservation) => void;
  onCancelled: (answer: MerchantCancelOutput) => void;
};

function BookingBody({ slug, booking, slotLengthMin, started, ended, onMoved, onCancelled }: BookingBodyProps) {
  const client = useClient();
  const toast = useToast();
  const live = booking.status === "confirmed";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { free, chosen, setChosen } = useFreeTimes(slug, booking, live && !started, setError);
  const keyFor = useIdempotencyKey();

  // A write keeps the dialog busy; a failure shows its error, and the merchant may try again.
  async function write(run: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await run();
    } catch (failure) {
      setError(errorMessage(failure));
      setBusy(false);
    }
  }

  async function move(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const slot = free?.find((s) => s.time === chosen);
    if (!slot || busy) return;
    await write(async () => {
      const moved = await client.moveBooking(slug, booking.id, {
        starts_at: slot.starts_at,
        idempotency_key: keyFor({ id: booking.id, starts_at: slot.starts_at }),
      });
      toast(
        <>
          <b>{moved.guest_name} moved</b>
          {moved.time}, table {moved.table_label}
        </>,
      );
      onMoved(moved);
    });
  }

  async function cancel() {
    if (busy) return;
    await write(async () => {
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
    });
  }

  return (
    <>
      <Facts booking={booking} slotLengthMin={slotLengthMin} live={live} />
      {live && !started && (
        <MoveForm
          times={free?.map((slot) => slot.time) ?? null}
          chosen={chosen}
          busy={busy}
          onChoose={setChosen}
          onMove={move}
        />
      )}
      {error && (
        <p className="note bad" role="alert">
          {error}
        </p>
      )}
      {live && <CancelControls note={cancelNote(booking, ended)} busy={busy} onCancel={cancel} />}
    </>
  );
}

type FreeTimes = {
  // The other free starts of the day, with their instant as the server words it.
  free: DaySlot[] | null;
  chosen: string | null;
  setChosen: (time: string) => void;
};

// The day's other free starts for this party, the nearest one chosen; loaded only while it can move.
function useFreeTimes(
  slug: string,
  booking: ManagedReservation,
  movable: boolean,
  setError: (error: string) => void,
): FreeTimes {
  const client = useClient();
  const [free, setFree] = useState<DaySlot[] | null>(null);
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
    if (!movable) return;
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
  }, [movable, loadTimes, booking.time, setError]);
  return { free, chosen, setChosen };
}

type FactsProps = { booking: ManagedReservation; slotLengthMin: number; live: boolean };

function Facts({ booking, slotLengthMin, live }: FactsProps) {
  const facts: [string, string][] = [
    ["When", whenLine(booking, slotLengthMin)],
    ["Party", guests(booking.party_size)],
    ["Table", booking.table_label],
    ["Booked", bookedLine(booking)],
    ["Reference", booking.id],
  ];
  if (!live) facts.push(["Status", `Cancelled by ${booking.cancelled_by === "guest" ? "the guest" : "you"}`]);
  return (
    <dl className="facts">
      {facts.map(([term, value]) => (
        <div key={term}>
          <dt>{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

type MoveFormProps = {
  // Null while they load.
  times: string[] | null;
  chosen: string | null;
  busy: boolean;
  onChoose: (time: string) => void;
  onMove: (event: FormEvent<HTMLFormElement>) => void;
};

function MoveForm({ times, chosen, busy, onChoose, onMove }: MoveFormProps) {
  return (
    <form onSubmit={onMove}>
      <label className="field">
        Move to another time
        <span className="move">
          <select
            aria-label="Move to another time"
            disabled={!times?.length}
            value={times?.length ? (chosen ?? "") : ""}
            onChange={(event) => onChoose(event.target.value)}
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
  );
}

type CancelControlsProps = { note: string; busy: boolean; onCancel: () => void };

// The cancellation, asked twice.
function CancelControls({ note, busy, onCancel }: CancelControlsProps) {
  const [confirming, setConfirming] = useState(false);
  if (!confirming) {
    return (
      <div className="dlg-foot">
        <Pill small onClick={() => setConfirming(true)}>
          Cancel the booking
        </Pill>
      </div>
    );
  }
  return (
    <>
      <p className="note">
        <b>Cancel this booking?</b>{" "}
        {note}
      </p>
      <div className="dlg-foot">
        <Pill small onClick={() => setConfirming(false)}>
          Keep it
        </Pill>
        <Pill small primary disabled={busy} onClick={onCancel}>
          Cancel the booking
        </Pill>
      </div>
    </>
  );
}
