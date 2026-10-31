// Booking form of reservation.html. It posts to the platform's public API (issue #15); when
// that API is missing, the form switches to "call us" mode, so the site works on its own.
"use strict";

const API_URL = "http://localhost:8002/public/le-petit-zinc/reservations";
const PHONE = "03 89 00 00 00";
const TIME_ZONE = "Europe/Zurich"; // MERCHANT_TZ: same offsets as Mulhouse
const SERVICES = [
  { label: "Déjeuner", start: "12:00", end: "14:00" },
  { label: "Dîner", start: "19:00", end: "22:00" },
];
// AGENTS.md §6: a start time every 30 minutes, last seating one hour before the service ends.
const STEP_MIN = 30;
const LAST_SEATING_MIN = 60;
const TIMEOUT_MS = 8000;

const form = document.querySelector("#booking-form");
const dateInput = form.elements.date;
const timeSelect = form.elements.time;
const submitButton = form.querySelector("button[type=submit]");
const statusBox = document.querySelector("#booking-status");

const dayFormat = new Intl.DateTimeFormat("fr-FR", {
  timeZone: TIME_ZONE,
  day: "2-digit",
  month: "2-digit",
});
const timeFormat = new Intl.DateTimeFormat("fr-FR", {
  timeZone: TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
});

let pending = null; // { signature, key } of the last request sent

const toMinutes = (hhmm) => {
  const [hours, minutes] = hhmm.split(":").map(Number);
  return hours * 60 + minutes;
};

const toHHMM = (minutes) =>
  `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;

const display = (hhmm) => hhmm.replace(":", "h");

/** Today's date ("YYYY-MM-DD") and time (minutes since midnight) at the restaurant. */
function restaurantNow() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date());
  const part = (type) => parts.find((p) => p.type === type).value;
  return {
    day: `${part("year")}-${part("month")}-${part("day")}`,
    minutes: Number(part("hour")) * 60 + Number(part("minute")),
  };
}

function addDays(day, count) {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + count);
  return date.toISOString().slice(0, 10);
}

/** UTC offset of the restaurant on that day, e.g. "+01:00". Services run between noon and
 * 22:00, never across a daylight-saving switch (at night), so the noon offset holds all day. */
function utcOffset(day) {
  const name = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, timeZoneName: "longOffset" })
    .formatToParts(new Date(`${day}T12:00:00Z`))
    .find((p) => p.type === "timeZoneName").value; // "GMT+01:00"
  return name === "GMT" ? "+00:00" : name.slice(3);
}

function startTimes(service) {
  const times = [];
  for (let m = toMinutes(service.start); m <= toMinutes(service.end) - LAST_SEATING_MIN; m += STEP_MIN) {
    times.push(toHHMM(m));
  }
  return times;
}

/** Fill the time list for the chosen day; on today, times already past are disabled. */
function renderTimes() {
  const now = restaurantNow();
  const isToday = dateInput.value === now.day;
  const previous = timeSelect.value;
  const placeholder = new Option("Choisir une heure", "");
  timeSelect.replaceChildren(placeholder);
  for (const service of SERVICES) {
    const group = document.createElement("optgroup");
    group.label = `${service.label} (${display(service.start)} – ${display(service.end)})`;
    for (const time of startTimes(service)) {
      const option = new Option(display(time), time);
      option.disabled = isToday && toMinutes(time) <= now.minutes;
      group.append(option);
    }
    timeSelect.append(group);
  }
  const kept = [...timeSelect.options].find((o) => o.value === previous && !o.disabled);
  timeSelect.value = kept ? previous : "";
}

/** Today, or tomorrow once tonight's last seating has passed. */
function resetDate() {
  const now = restaurantNow();
  const lastSeating = Math.max(...SERVICES.flatMap(startTimes).map(toMinutes));
  dateInput.min = now.day;
  dateInput.value = now.minutes < lastSeating ? now.day : addDays(now.day, 1);
  renderTimes();
}

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID(); // secure contexts only (localhost is one)
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** A new key for each new request; the same key when the same request is sent again (after a
 * timeout, a double click…), so that a retry can never book twice. */
function idempotencyKey(booking) {
  const signature = JSON.stringify(booking);
  if (pending?.signature !== signature) pending = { signature, key: uuid() };
  return pending.key;
}

function show(kind, text) {
  statusBox.className = `notice notice-${kind}`;
  statusBox.textContent = text;
  statusBox.hidden = false;
}

function setBusy(busy) {
  submitButton.disabled = busy;
  submitButton.textContent = busy ? "Envoi en cours…" : "Confirmer la réservation";
}

const guests = (count) => (count === 1 ? "1 personne" : `${count} personnes`);

/** Best effort: the largest number under the party size in the error message ("… max 6 …"). */
function maxPartySize(message, partySize) {
  const numbers = (String(message ?? "").match(/\d+/g) ?? []).map(Number);
  const candidates = numbers.filter((n) => n >= 1 && n < partySize);
  return candidates.length ? Math.max(...candidates) : null;
}

function handleResponse(status, body, booking) {
  if (status === 201) {
    const reservation = body.reservation ?? body;
    const startsAt = new Date(reservation.starts_at);
    pending = null;
    form.reset();
    resetDate();
    show(
      "success",
      `Merci ! Réservation ${reservation.id} confirmée pour ${guests(reservation.party_size)} ` +
        `le ${dayFormat.format(startsAt)} à ${display(timeFormat.format(startsAt))}.`,
    );
  } else if (status === 404) {
    show("info", `Réservation en ligne bientôt disponible — appelez-nous au ${PHONE}.`);
  } else if (status === 409) {
    show("error", "Ce créneau n'est plus disponible, essayez une autre heure.");
  } else if (status === 422 && body.error === "party_too_large") {
    const max = maxPartySize(body.message, booking.party_size);
    show(
      "error",
      max
        ? `Pour les groupes de plus de ${guests(max)}, appelez-nous au ${PHONE}.`
        : `Pour les groupes, appelez-nous au ${PHONE}.`,
    );
  } else {
    show("error", `La réservation n'a pas pu être enregistrée. Appelez-nous au ${PHONE}.`);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(form);
  const day = data.get("date");
  const booking = {
    starts_at: `${day}T${data.get("time")}:00${utcOffset(day)}`,
    party_size: Number(data.get("party_size")),
    guest_name: String(data.get("guest_name")).trim(),
  };
  const payload = { ...booking, idempotency_key: idempotencyKey(booking) };

  setBusy(true);
  show("info", "Réservation en cours…");
  try {
    const response = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout?.(TIMEOUT_MS),
    });
    const body = await response.json().catch(() => ({}));
    handleResponse(response.status, body, booking);
  } catch {
    // No backend, timeout or CORS refusal: the site still answers, in "call us" mode.
    show("info", `La réservation en ligne est momentanément indisponible — appelez-nous au ${PHONE}.`);
  } finally {
    setBusy(false);
  }
});

dateInput.addEventListener("change", renderTimes);
resetDate();
