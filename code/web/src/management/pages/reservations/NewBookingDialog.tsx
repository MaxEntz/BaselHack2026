import { type FormEvent, useEffect, useRef, useState } from "react";
import type { DaySlot, ManagedReservation, Service } from "../../../types.ts";
import { useClient } from "../../data.ts";
import { dayLong } from "../../dates.ts";
import { useIdempotencyKey } from "../../keys.ts";
import { errorMessage } from "../../problems.ts";
import { Dialog, Pill, useToast } from "../../ui/index.ts";
import { guests, newBookingNote } from "./words.ts";

// The most a slots query takes (core.forms.MAX_SEATS).
const MAX_PARTY_QUERY = 1000;

export type NewBookingPreset = { time?: string; party?: number };

/**
 * A booking the merchant enters, on `day`: only its starts still to come and not blocked are
 * offered, with a live note on the table it would get.
 */
export function NewBookingDialog({
  slug,
  day,
  limit,
  service,
  preset,
  open,
  onClose,
  onAdded,
}: {
  slug: string;
  day: string;
  // min(max_party_size, largest table)
  limit: number;
  // The service shown on the page: its first free start is the default time.
  service: Service | null;
  preset: NewBookingPreset;
  open: boolean;
  onClose: () => void;
  onAdded: (booking: ManagedReservation) => void;
}) {
  return (
    <Dialog open={open} onClose={onClose} title="A new booking">
      {open && (
        <NewBookingForm
          slug={slug}
          day={day}
          limit={limit}
          service={service}
          preset={preset}
          onCancel={onClose}
          onAdded={onAdded}
        />
      )}
    </Dialog>
  );
}

function NewBookingForm({
  slug,
  day,
  limit,
  service,
  preset,
  onCancel,
  onAdded,
}: {
  slug: string;
  day: string;
  limit: number;
  service: Service | null;
  preset: NewBookingPreset;
  onCancel: () => void;
  onAdded: (booking: ManagedReservation) => void;
}) {
  const client = useClient();
  const toast = useToast();
  const [guest, setGuest] = useState("");
  const [party, setParty] = useState(String(preset.party ?? 2));
  const [slots, setSlots] = useState<DaySlot[] | null>(null);
  const [time, setTime] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const name = useRef<HTMLInputElement>(null);
  const keyFor = useIdempotencyKey();

  const size = Number(party);
  // The starts don't depend on the party: an invalid one still lists them, for 1 guest.
  const asked = size >= 1 && Number.isInteger(size) ? Math.min(size, MAX_PARTY_QUERY) : 1;

  useEffect(() => {
    name.current?.focus();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    client.slots(slug, { date: day, partySize: asked }, controller.signal).then(
      (answer) => {
        if (controller.signal.aborted) return;
        const offered = answer.slots.filter((slot) => !slot.blocked);
        setSlots(answer.slots);
        setTime((current) => {
          if (current && offered.some((slot) => slot.time === current)) return current;
          const inService = offered.find(
            (slot) => service !== null && service.start <= slot.time && slot.time < service.end,
          );
          const preferred = offered.find((slot) => slot.time === preset.time) ?? inService ?? offered[0];
          return preferred?.time ?? null;
        });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setFailure(errorMessage(error));
      },
    );
    return () => controller.abort();
  }, [client, slug, day, asked, service, preset.time]);

  const note = newBookingNote({ slots, time, party: size, limit });

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if (!guest.trim()) {
      setFailure("Add the guest's name.");
      name.current?.focus();
      return;
    }
    const slot = slots?.find((s) => s.time === time);
    if (!note.ok || !slot) return;
    setBusy(true);
    setFailure(null);
    try {
      const payload = { starts_at: slot.starts_at, party_size: size, guest_name: guest.trim() };
      const booked = await client.addBooking(slug, { ...payload, idempotency_key: keyFor(payload) });
      toast(
        <>
          <b>{booked.guest_name} is booked</b>
          {booked.time}, {guests(booked.party_size)}, table {booked.table_label}
        </>,
      );
      onAdded(booked);
    } catch (error) {
      setFailure(errorMessage(error));
      setBusy(false);
    }
  }

  // A failed save or a missing name, else the live note.
  const shown = failure ?? note.text;
  const bad = failure !== null || !note.ok;
  return (
    <form className="dlg-form" noValidate onSubmit={add}>
      <p>{dayLong(day)}</p>
      <label className="field">
        Guest's name
        <input
          ref={name}
          type="text"
          autoComplete="off"
          maxLength={200}
          value={guest}
          onChange={(event) => {
            setGuest(event.target.value);
            setFailure(null);
          }}
        />
      </label>
      <div className="two">
        <label className="field">
          Guests
          <input
            type="number"
            min={1}
            value={party}
            onChange={(event) => {
              setParty(event.target.value);
              setFailure(null);
            }}
          />
        </label>
        <label className="field">
          Time
          <select
            value={time ?? ""}
            onChange={(event) => {
              setTime(event.target.value);
              setFailure(null);
            }}
          >
            {slots
              ?.filter((slot) => !slot.blocked)
              .map((slot) => (
                <option key={slot.time}>{slot.time}</option>
              ))}
          </select>
        </label>
      </div>
      <p className={bad ? "note bad" : "note"} role="status">
        {shown}
      </p>
      <div className="dlg-foot">
        <Pill small onClick={onCancel}>
          Not now
        </Pill>
        <Pill small primary type="submit" disabled={busy}>
          Add the booking
        </Pill>
      </div>
    </form>
  );
}
