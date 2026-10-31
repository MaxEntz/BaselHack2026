import "./pages.css";
import { type FormEvent, useState } from "react";
import type { BlockedTimes, Service } from "../types.ts";
import { useClient } from "./data.ts";
import { dayLong } from "./dates.ts";
import { errorMessage } from "./problems.ts";
import { serviceNames, startsOf } from "./services.ts";
import { Dialog, Pill, useToast } from "./ui/index.ts";

/** Sets one day's blocked times: a checkbox pill per start, grouped by service. */
export function BlockDialog({
  slug,
  date,
  services,
  blocked,
  open,
  onClose,
  onSaved,
}: {
  slug: string;
  date: string;
  services: readonly Service[];
  // The day's blocked times, HH:MM.
  blocked: readonly string[];
  open: boolean;
  onClose: () => void;
  onSaved: (blocked: BlockedTimes) => void;
}) {
  const client = useClient();
  const toast = useToast();
  const [error, setError] = useState<string | null>(null);
  const names = serviceNames(services);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const times = new FormData(event.currentTarget).getAll("time").map(String).toSorted();
    try {
      onSaved(await client.setBlocked(slug, date, times));
    } catch (failure) {
      setError(errorMessage(failure));
      return;
    }
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
        {services.map((service, index) => (
          <fieldset key={service.start}>
            <legend>{names[index]}</legend>
            <div className="pick">
              {startsOf(service).map((time) => (
                <label key={time}>
                  <input type="checkbox" name="time" value={time} defaultChecked={blocked.includes(time)} />
                  <span>{time}</span>
                </label>
              ))}
            </div>
          </fieldset>
        ))}
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
