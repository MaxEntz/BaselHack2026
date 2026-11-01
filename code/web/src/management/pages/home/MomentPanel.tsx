import { plural } from "../../../cards/format.ts";
import type { MomentAt } from "../../../moments.ts";
import type { ManagedReservation, Rules, Service } from "../../../types.ts";
import { Box, Source } from "../../ui/index.ts";
import { bookings, guests, minutes, serviceWord, throughAgent } from "./format.ts";

const services = (n: number) => plural(n, "service");
const seats = (n: number) => plural(n, "seat");
const capitalized = (text: string) => `${text.charAt(0).toUpperCase()}${text.slice(1)}`;

type MomentPanelProps = {
  at: MomentAt;
  // `true`: asked for from Studio, not the clock's.
  previewed: boolean;
  samples: boolean;
  allServices: readonly Service[];
  reservations: readonly ManagedReservation[];
  blocked: readonly string[];
  slotLength: number;
  seatCount: number;
  largestTable: number;
  rules: Rules;
};

const inService = (reservations: readonly ManagedReservation[], service: Service) =>
  reservations
    .filter(
      (booking) =>
        booking.status === "confirmed" &&
        minutes(booking.time) >= minutes(service.start) &&
        minutes(booking.time) < minutes(service.end),
    )
    .toSorted((a, b) => a.time.localeCompare(b.time) || a.id.localeCompare(b.id));

const partyOf = (list: readonly ManagedReservation[]) =>
  list.reduce((sum, booking) => sum + booking.party_size, 0);

/** The top of Home, by the hour (#154): what the merchant needs at that moment of the day. */
export function MomentPanel(props: MomentPanelProps) {
  const { at, previewed, samples, reservations } = props;
  const list = inService(reservations, at.service);
  const note = samples ? ", samples" : "";
  return (
    <Box tone="olive" className="moment" aria-label="Right now" data-moment={at.moment}>
      {previewed && <small className="moment-at num">As at {at.time}</small>}
      {at.moment === "morning" && <Morning {...props} note={note} />}
      {at.moment === "before" && <Before {...props} list={list} note={note} />}
      {at.moment === "during" && <During {...props} list={list} note={note} />}
      {at.moment === "after" && <After {...props} list={list} note={note} />}
    </Box>
  );
}

type PartProps = MomentPanelProps & { note: string };
type ListProps = PartProps & { list: readonly ManagedReservation[] };

function Morning({ allServices, reservations, blocked, seatCount, largestTable, rules, at, note }: PartProps) {
  const tonight = blocked.filter(
    (time) => minutes(time) >= minutes(at.service.start) && minutes(time) < minutes(at.service.end),
  );
  const checks = [
    ...(rules.cancellation_policy ? [] : ["No cancellation policy yet. Guests see none."]),
    ...(tonight.length ? [`${tonight.join(", ")} blocked tonight.`] : []),
    ...(rules.max_party_size > largestTable
      ? [`Groups up to ${rules.max_party_size}, but your largest table seats ${largestTable}.`]
      : []),
  ];
  return (
    <>
      <h2>Today{note}</h2>
      <p className="moment-sum">
        {services(allServices.length)}, {seats(seatCount)}.
      </p>
      {allServices.map((service) => {
        const list = inService(reservations, service);
        return (
          <div key={service.start} className="moment-svc">
            <b>{capitalized(serviceWord(service))}</b>
            <span className="num">
              {service.start}–{service.end}
            </span>
            <small className="num">
              {bookings(list.length)}, {guests(partyOf(list))}
            </small>
          </div>
        );
      })}
      {checks.length > 0 && (
        <ul className="moment-checks">
          {checks.map((check) => (
            <li key={check}>{check}</li>
          ))}
        </ul>
      )}
    </>
  );
}

function Before({ list, seatCount, note }: ListProps) {
  return (
    <>
      <h2>Tonight{note}</h2>
      <p className="moment-sum">
        {bookings(list.length)}, {guests(partyOf(list))} of {seatCount} seats.
      </p>
      <table className="moment-rows">
        <tbody>
          {list.map((booking) => (
            <tr key={booking.id}>
              <td>{booking.time}</td>
              <td>{booking.guest_name}</td>
              <td className="num">{booking.party_size}</td>
              <td className="num">{booking.table_label}</td>
              <td>
                <Source via={booking.via} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function During({ at, list, slotLength, note }: ListProps) {
  const now = minutes(at.time);
  const coming = list.filter((booking) => minutes(booking.time) > now);
  const seated = list.filter(
    (booking) => minutes(booking.time) <= now && now < minutes(booking.time) + slotLength,
  );
  const [next, then] = coming;
  return (
    <>
      {next ? (
        <>
          <p className="moment-sum">Next{note}</p>
          <div className="moment-time num">{next.time}</div>
          <div className="moment-who">{next.guest_name}</div>
          <div className="moment-sub">
            {guests(next.party_size)}, table {next.table_label}
          </div>
        </>
      ) : (
        <div className="moment-who">No more arrivals tonight.</div>
      )}
      {then && (
        <p className="moment-then">
          Then {then.time}, {then.guest_name}, {guests(then.party_size)}
        </p>
      )}
      <p className="moment-then">
        Seated now:{" "}
        {seated.length > 0
          ? seated.map((booking) => `${booking.table_label} ${booking.guest_name}`).join(", ")
          : "nobody"}
      </p>
    </>
  );
}

function After({ at, list, note }: ListProps) {
  const agent = list.filter((booking) => throughAgent(booking.via)).length;
  return (
    <>
      <h2>Tonight, done{note}</h2>
      <p className="moment-sum">
        {capitalized(serviceWord(at.service))} service, {at.service.start}–{at.service.end}.
      </p>
      <div className="moment-stats">
        <div>
          <b className="num">{partyOf(list)}</b>
          <small>guests</small>
        </div>
        <div>
          <b className="num">{list.length}</b>
          <small>bookings</small>
        </div>
        <div>
          <b className="num">{agent}</b>
          <small>through the agent</small>
        </div>
      </div>
    </>
  );
}
