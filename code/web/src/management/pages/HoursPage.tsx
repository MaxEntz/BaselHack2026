import { useCallback, useState } from "react";
import type { BlockedTime, Hours, Service } from "../../types.ts";
import { BlockDialog } from "../BlockDialog.tsx";
import { useClient, useLoad } from "../data.ts";
import { dayShort } from "../dates.ts";
import { LeaveGuard } from "../LeaveGuard.tsx";
import { PAGES } from "../pages.ts";
import { errorMessage } from "../problems.ts";
import { lastSeating, serviceNames } from "../services.ts";
import { Pill, SaveBar, useToast } from "../ui/index.ts";
import { useSavedForm } from "../useSavedForm.tsx";
import { PageTitle } from "./PageTitle.tsx";

type HoursData = { hours: Hours; blocked: BlockedTime[]; today: string; clock: string };

export function HoursPage({ slug }: { slug: string }) {
  const client = useClient();
  const load = useCallback(
    async (signal: AbortSignal): Promise<HoursData> => {
      const [hours, { blocked }, merchant] = await Promise.all([
        client.hours(slug, signal),
        client.blocked(slug, signal),
        client.merchant(slug, signal),
      ]);
      // The merchant's today and time are the platform's, never the browser's.
      return { hours, blocked, today: merchant.now.slice(0, 10), clock: merchant.now.slice(11, 16) };
    },
    [client, slug],
  );
  const { data, error, update } = useLoad(load);

  return (
    <>
      <PageTitle title={PAGES.hours} />
      {error && (
        <p className="err" role="alert">
          {error}
        </p>
      )}
      {data && (
        <>
          <ServicesForm
            slug={slug}
            services={data.hours.services}
            onSaved={(hours) => update((loaded) => ({ ...loaded, hours }))}
          />
          <BlockedTimes
            slug={slug}
            today={data.today}
            clock={data.clock}
            services={data.hours.services}
            blocked={data.blocked}
            onChange={(blocked) => update((loaded) => ({ ...loaded, blocked }))}
          />
        </>
      )}
    </>
  );
}

function ServicesForm({
  slug,
  services,
  onSaved,
}: {
  slug: string;
  services: Service[];
  onSaved: (hours: Hours) => void;
}) {
  const client = useClient();
  const form = useSavedForm(
    services,
    async (draft) => {
      const hours = await client.saveHours(slug, {
        services: draft.map(({ start, end }) => ({ start: start || null, end: end || null })),
      });
      onSaved(hours);
      return hours.services;
    },
    serviceNames,
  );
  const names = serviceNames(form.saved);
  const change = (index: number, key: keyof Service, value: string) =>
    form.set(form.value.map((service, i) => (i === index ? { ...service, [key]: value } : service)));

  return (
    <form className="forms" noValidate onSubmit={form.submit}>
      <div className="fs">
        <h3>Services</h3>
        <p>Open every day. Guests can book every 30 minutes, up to an hour before the end of a service.</p>
        <div className="svs">
          {form.value.map((service, index) => (
            <div className="sv" key={index}>
              <b>{names[index]}</b>
              <div>
                <label className="sr" htmlFor={`sv-s-${index}`}>
                  {names[index]} starts
                </label>
                <input
                  id={`sv-s-${index}`}
                  type="time"
                  step={1800}
                  value={service.start}
                  onChange={(event) => change(index, "start", event.target.value)}
                />
              </div>
              <div>
                <label className="sr" htmlFor={`sv-e-${index}`}>
                  {names[index]} ends
                </label>
                <input
                  id={`sv-e-${index}`}
                  type="time"
                  step={1800}
                  value={service.end}
                  onChange={(event) => change(index, "end", event.target.value)}
                />
              </div>
              <small className="num">Last table {lastSeating(service) ?? "none"}</small>
            </div>
          ))}
        </div>
      </div>
      <SaveBar dirty={form.dirty} error={form.error} onDiscard={form.discard} />
      <LeaveGuard dirty={form.dirty} />
    </form>
  );
}

function BlockedTimes({
  slug,
  today,
  clock,
  services,
  blocked,
  onChange,
}: {
  slug: string;
  today: string;
  clock: string;
  services: Service[];
  blocked: BlockedTime[];
  onChange: (blocked: BlockedTime[]) => void;
}) {
  const client = useClient();
  const toast = useToast();
  const [blocking, setBlocking] = useState(false);
  const sorted = blocked.toSorted((a, b) => a.starts_at.localeCompare(b.starts_at));
  const timesOn = (date: string) => blocked.filter((b) => b.date === date).map((b) => b.time);

  async function reopen({ date, time }: BlockedTime) {
    try {
      const answer = await client.setBlocked(
        slug,
        date,
        timesOn(date).filter((other) => other !== time),
      );
      onChange(answer.blocked);
      toast("It's open again.");
    } catch (failure) {
      toast(errorMessage(failure));
    }
  }

  return (
    <div className="fs">
      <h3>Blocked times</h3>
      <p>Agents don't offer these. Guests already booked keep their table.</p>
      {sorted.length ? (
        <div className="pills">
          {sorted.map((slot) => (
            <Pill small key={slot.starts_at} onClick={() => reopen(slot)}>
              {dayShort(slot.date)}, {slot.time}, open it again
            </Pill>
          ))}
        </div>
      ) : (
        <p>Nothing is blocked.</p>
      )}
      <div>
        <Pill onClick={() => setBlocking(true)}>Block a time</Pill>
      </div>
      <BlockDialog
        slug={slug}
        date={today}
        services={services}
        blocked={timesOn(today)}
        after={clock}
        open={blocking}
        onClose={() => setBlocking(false)}
        onSaved={(answer) => onChange(answer.blocked)}
      />
    </div>
  );
}
