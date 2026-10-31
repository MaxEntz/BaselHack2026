import type { DiffCardData, Policy } from "../types.ts";
import { formatMinutes, formatServices, plural } from "./format.ts";

export function PolicyCard({ data }: { data: Policy }) {
  return (
    <section className="card" aria-label="Booking policy">
      <h2 className="card-title">Booking policy</h2>
      <dl className="facts">
        <div className="fact">
          <dt>Max party size</dt>
          <dd>{plural(data.max_party_size, "guest")}</dd>
        </div>
        <div className="fact">
          <dt>Booking duration</dt>
          <dd>{formatMinutes(data.slot_length_min)}</dd>
        </div>
        <div className="fact">
          <dt>Services</dt>
          <dd>{formatServices(data.services)}</dd>
        </div>
      </dl>
    </section>
  );
}

export function ConfirmationCard({ data }: { data: { text: string } }) {
  return (
    <div className="confirmation" role="status">
      <span aria-hidden="true">✓</span> {data.text}
    </div>
  );
}

// A change, as code words it (#152): the old value struck through, the new one bold.
export function DiffCard({ data }: { data: DiffCardData }) {
  return (
    <div className="confirmation" role="status">
      <span aria-hidden="true">✓</span> {data.label}:{" "}
      {data.before && (
        <>
          <s>{data.before}</s> →{" "}
        </>
      )}
      <b>{data.after}</b>
    </div>
  );
}
