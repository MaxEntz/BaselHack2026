import "./pages.css";
import { type FormEvent, useState } from "react";
import type { BlockedTimes, Service } from "../types.ts";
import { useClient } from "./data.ts";
import { dayLong } from "./dates.ts";
import { errorMessage } from "./problems.ts";
import { serviceNames, startsOf } from "./services.ts";
import { Dialog, Pill, useToast } from "./ui/index.ts";

type BlockDialogProps = {
  slug: string;
  date: string;
  services: readonly Service[];
  // The day's blocked times, HH:MM.
  blocked: readonly string[];
  // The merchant's time now (HH:MM) when `date` is today: starts up to it have passed, and the
  // server ignores them, so they aren't offered.
  after?: string | null;
  open: boolean;
  onClose: () => void;
  // The server's answer, for a page that shows the blocked times. Without it, the page reads
  // them again through the live refresh, like after any change (#137).
  onSaved?: (blocked: BlockedTimes) => void;
};

/** Sets one day's blocked times: a checkbox pill per start, grouped by service. */
export function BlockDialog({ slug, date, services, blocked, after = null, open, onClose, onSaved }: BlockDialogProps) {
  const client = useClient();
  const toast = useToast();
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const checked = new FormData(event.currentTarget).getAll("time").map(String);
    let answer: BlockedTimes;
    try {
      answer = await client.setBlocked(slug, date, checked);
    } catch (failure) {
      setError(errorMessage(failure));
      return;
    }
    onSaved?.(answer);
    // Worded from what the server kept: it ignores a time that has passed meanwhile (#133).
    const times = answer.blocked
      .filter((b) => b.date === date)
      .map((b) => b.time)
      .toSorted();
    toast(
      times.length ? (
        <>
          <b>Blocked {times.join(", ")}</b>Agents stop offering {times.length === 1 ? "it" : "them"}.
        </>
      ) : (
        "No time is blocked on this day."
      ),
    );
    close();
  }

  function close() {
    setError(null);
    onClose();
  }

  return (
    <Dialog open={open} onClose={close} title="Block a time">
      <form className="dlg-form" onSubmit={save}>
        <p>
          {dayLong(date)}. Agents stop offering these times, and the guests already booked keep their table.
        </p>
        <Starts services={services} blocked={blocked} after={after} />
        {error && (
          <p className="note bad" role="alert">
            {error}
          </p>
        )}
        <div className="dlg-foot">
          <Pill small onClick={close}>
            Not now
          </Pill>
          <Pill small primary type="submit">
            Save
          </Pill>
        </div>
      </form>
    </Dialog>
  );
}

type StartsProps = { services: readonly Service[]; blocked: readonly string[]; after: string | null };

// A checkbox per start still to come, a fieldset per service.
function Starts({ services, blocked, after }: StartsProps) {
  const names = serviceNames(services);
  return services.map((service, index) => (
    <fieldset key={service.start}>
      <legend>{names[index]}</legend>
      <div className="pick">
        {startsOf(service)
          .filter((time) => after === null || time > after)
          .map((time) => (
            <label key={time}>
              <input type="checkbox" name="time" value={time} defaultChecked={blocked.includes(time)} />
              <span>{time}</span>
            </label>
          ))}
      </div>
    </fieldset>
  ));
}
