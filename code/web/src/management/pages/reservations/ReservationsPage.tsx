import "./reservations.css";
import { useCallback, useState } from "react";
import { useSearchParams } from "react-router";
import type { ManagedReservation, ReservationDay, Via } from "../../../types.ts";
import { BlockDialog } from "../../BlockDialog.tsx";
import { DayBar } from "../../DayBar.tsx";
import { useClient, useLoad } from "../../data.ts";
import { addDays } from "../../dates.ts";
import { useFresh } from "../../fresh.ts";
import { PAGES } from "../../pages.ts";
import { defaultService, serviceNames, serviceOf } from "../../services.ts";
import { Blob, Pill } from "../../ui/index.ts";
import { throughAgent } from "../home/format.ts";
import { PageTitle } from "../PageTitle.tsx";
import { BookingDialog } from "./BookingDialog.tsx";
import { NewBookingDialog } from "./NewBookingDialog.tsx";
import { columnTitle, dayLabel, rowNote, summary } from "./words.ts";

const DAYS_AHEAD = 7;

type Who = "" | "agent" | "merchant" | "website";
const WHO: [Who, string][] = [
  ["", "Everyone"],
  ["agent", "Agent"],
  ["merchant", "You"],
  ["website", "Website"],
];

function booked(who: Who, via: Via): boolean {
  if (who === "agent") return throughAgent(via);
  return who === "" || via === who;
}

const byTime = (a: ManagedReservation, b: ManagedReservation) =>
  a.time.localeCompare(b.time) || a.guest_name.localeCompare(b.guest_name);

type Base = { today: string; clock: string };

/**
 * One day's bookings, by service and start time, with the booking, new-booking and block dialogs
 * (#139). The URL says the day and which dialog is open (`?date=&booking=`, `&new=HH:MM&guests=`,
 * `&block=1`), so Home, the search, the floor plan and a live toast link straight to them.
 */
export function ReservationsPage({ slug, version }: { slug: string; version: number }) {
  const client = useClient();
  const load = useCallback(
    async (signal: AbortSignal): Promise<Base> => {
      const merchant = await client.merchant(slug, signal);
      // The merchant's today and time are the platform's, never the browser's.
      return { today: merchant.now.slice(0, 10), clock: merchant.now.slice(11, 16) };
    },
    [client, slug],
  );
  const { data, error } = useLoad(load, 0, false);
  return (
    <>
      <PageTitle title={PAGES.reservations} />
      {error && (
        <p className="err" role="alert">
          {error}
        </p>
      )}
      {data && <Day slug={slug} version={version} {...data} />}
    </>
  );
}

function Day({ slug, version, today, clock }: Base & { slug: string; version: number }) {
  const client = useClient();
  const [params, setParams] = useSearchParams();
  const days = Array.from({ length: DAYS_AHEAD }, (_, i) => addDays(today, i));
  const asked = params.get("date");
  const day = asked && days.includes(asked) ? asked : today;
  const [chosenService, setChosenService] = useState<{ day: string; index: number } | null>(null);
  const [who, setWho] = useState<Who>("");
  const [moved, setMoved] = useState<string | null>(null);

  const load = useCallback(
    (signal: AbortSignal) => client.reservations(slug, day, signal),
    [client, slug, day],
  );
  const { data, error } = useLoad(load, version);
  const current = data?.date === day ? data : null;
  const fresh = useFresh(current ? current.reservations.map((b) => b.id) : null, day);

  // Every dialog lives in the URL, beside the day.
  const go = (changes: Record<string, string | null>) =>
    setParams(
      (now) => {
        const next = new URLSearchParams(now);
        for (const [key, value] of Object.entries(changes)) {
          if (value === null) next.delete(key);
          else next.set(key, value);
        }
        return next;
      },
      { replace: true },
    );
  const closeAll = { booking: null, new: null, guests: null, block: null };

  if (error) {
    return (
      <p className="err" role="alert">
        {error}
      </p>
    );
  }
  // While another day loads, the last one stays.
  if (!data) return null;

  const isToday = data.date === today;
  const openId = params.get("booking");
  const opened = openId ? (data.reservations.find((b) => b.id === openId) ?? null) : null;
  const newAt = params.get("new");
  const services = data.services;
  // A link to a booking, or to a new one at a time, shows that time's service.
  const linked = serviceOf(services, opened?.time ?? newAt ?? "");
  const service =
    chosenService?.day === data.date
      ? chosenService.index
      : linked >= 0
        ? linked
        : defaultService(services, isToday, clock);
  const names = serviceNames(services);
  const showService = (time: string) => {
    const index = serviceOf(services, time);
    if (index >= 0) setChosenService({ day: data.date, index });
  };

  return (
    <>
      {services.length > 0 && (
        <DayBar
          days={days}
          day={data.date}
          onDay={(next) => {
            setChosenService(null);
            setMoved(null);
            go({ date: next, ...closeAll });
          }}
          services={services}
          service={service}
          onService={(index) => setChosenService({ day: data.date, index })}
        />
      )}
      <Bookings
        data={data}
        today={today}
        service={service}
        name={names[service] ?? ""}
        who={who}
        onWho={setWho}
        fresh={(id) => fresh.has(id) || id === moved}
        onOpen={(id) => go({ booking: id })}
        onBlock={() => go({ block: "1" })}
        onNew={() => go({ new: "", guests: null })}
      />
      <BookingDialog
        slug={slug}
        booking={opened}
        slotLengthMin={data.slot_length_min}
        started={opened !== null && (opened.date < today || (opened.date === today && opened.time <= clock))}
        onClose={() => go({ booking: null })}
        onMoved={(booking) => {
          setMoved(booking.id);
          showService(booking.time);
          go({ booking: null });
        }}
        onCancelled={() => go({ booking: null })}
      />
      <NewBookingDialog
        slug={slug}
        day={data.date}
        limit={data.largest_group}
        service={services[service] ?? null}
        preset={{ time: newAt || undefined, party: Number(params.get("guests")) || undefined }}
        open={newAt !== null}
        onClose={() => go({ new: null, guests: null })}
        onAdded={(booking) => {
          showService(booking.time);
          go({ new: null, guests: null });
        }}
      />
      <BlockDialog
        slug={slug}
        date={data.date}
        services={services}
        blocked={data.blocked}
        after={isToday ? clock : null}
        open={params.get("block") === "1"}
        onClose={() => go({ block: null })}
        onSaved={() => {}}
      />
    </>
  );
}

function Bookings({
  data,
  today,
  service,
  name,
  who,
  onWho,
  fresh,
  onOpen,
  onBlock,
  onNew,
}: {
  data: ReservationDay;
  today: string;
  service: number;
  name: string;
  who: Who;
  onWho: (who: Who) => void;
  fresh: (id: string) => boolean;
  onOpen: (id: string) => void;
  onBlock: () => void;
  onNew: () => void;
}) {
  const { services } = data;
  const range = services[service];
  const shown = data.reservations.filter((b) => booked(who, b.via)).toSorted(byTime);
  const here = range ? shown.filter((b) => range.start <= b.time && b.time < range.end) : [];
  // After a change of hours, a booking may fall in no service: it stays listed, last.
  const outside = shown.filter((b) => serviceOf(services, b.time) < 0);
  const times = [...new Set(here.map((b) => b.time))];
  const hasWebsite = data.reservations.some((b) => b.via === "website");

  return (
    <>
      <div className="bar-row">
        <p className="num">{summary(dayLabel(data.date, today, name), here)}</p>
        <div className="pills" role="group" aria-label="Booked by">
          {WHO.filter(([value]) => value !== "website" || hasWebsite).map(([value, label]) => (
            <Pill small key={label} aria-pressed={who === value} onClick={() => onWho(value)}>
              {label}
            </Pill>
          ))}
        </div>
        <div className="pills right">
          <Pill onClick={onBlock}>Block a time</Pill>
          <Pill primary onClick={onNew}>
            New booking
          </Pill>
        </div>
      </div>
      {times.length || outside.length ? (
        <div className="cols">
          {times.map((time, index) => {
            const at = here.filter((b) => b.time === time);
            const blocked = data.blocked.includes(time);
            return (
              <div className={blocked ? "col blocked" : "col"} key={time}>
                <div className="time-blob">
                  <Blob index={index} />
                  <b className="num">{time}</b>
                </div>
                <h3 className="num">{columnTitle(at, blocked)}</h3>
                <div>
                  {at.map((booking) => (
                    <Row key={booking.id} booking={booking} fresh={fresh(booking.id)} onOpen={onOpen} />
                  ))}
                </div>
              </div>
            );
          })}
          {outside.length > 0 && (
            <div className="col out">
              <h3>Outside your opening hours</h3>
              <div>
                {outside.map((booking) => (
                  <Row key={booking.id} booking={booking} fresh={fresh(booking.id)} onOpen={onOpen} withTime />
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        <p className="none">No bookings for this service.</p>
      )}
    </>
  );
}

function Row({
  booking,
  fresh,
  onOpen,
  withTime = false,
}: {
  booking: ManagedReservation;
  fresh: boolean;
  onOpen: (id: string) => void;
  withTime?: boolean;
}) {
  const live = booking.status === "confirmed";
  const className = ["who", !live && "off", fresh && "fresh"].filter(Boolean).join(" ");
  return (
    <button className={className} type="button" onClick={() => onOpen(booking.id)}>
      <span className="who-n">{booking.guest_name}</span>
      <span className="who-t">
        {withTime && `${booking.time}, `}
        {booking.party_size}, {booking.table_label}
      </span>
      <small className={live && throughAgent(booking.via) ? "c" : undefined}>{rowNote(booking)}</small>
    </button>
  );
}
