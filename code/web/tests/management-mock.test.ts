// The management mock client (#129) against the demo fixture: it applies the
// server's rules, so pages built on it behave like the real site.
import assert from "node:assert/strict";
import { test } from "node:test";

import fixture from "../../tests/fixtures/demo/la-graine-verte-management.json" with { type: "json" };
import { ManagementRequestError } from "../src/management/client.ts";
import { createMockManagementClient } from "../src/mocks/managementClient.ts";
import type { LiveEvent, ManagementFixture, Problem } from "../src/types.ts";

const SLUG = "la-graine-verte";
const TODAY = "2026-10-31";
const TOMORROW = "2026-11-01";
const data = fixture as ManagementFixture;
const RULES = {
  max_party_size: data.rules.max_party_size,
  fallback_url: data.rules.fallback_url,
  cancellation_policy: data.rules.cancellation_policy,
};
const SETTINGS = {
  name: data.settings.name,
  city: data.settings.city,
  price_level: data.settings.price_level,
  tags: data.settings.tags,
};
const client = (delay = 0) =>
  createMockManagementClient(data, { latencyMs: 0, liveBookingDelayMs: delay });
const clientOf = (changes: Partial<ManagementFixture>) =>
  createMockManagementClient({ ...data, ...changes }, { latencyMs: 0, liveBookingDelayMs: 0 });

async function refusal(promise: Promise<unknown>): Promise<ManagementRequestError> {
  const error = await promise.then(
    () => assert.fail("expected a refusal"),
    (reason: unknown) => reason,
  );
  assert.ok(error instanceof ManagementRequestError, String(error));
  return error;
}

async function problemOf(promise: Promise<unknown>): Promise<Problem | null | undefined> {
  const error = await refusal(promise);
  assert.equal(error.status, 422);
  return error.body.problem;
}

test("the mock answers Studio's public site and its frames", async () => {
  const page = { path: "/", width: 1000, height: 900, image: "data:image/jpeg;base64,AA==" };
  const withPages = createMockManagementClient(data, { latencyMs: 0, liveBookingDelayMs: 0, sitePages: [page] });
  assert.deepEqual((await withPages.site(SLUG)).pages, [page]);
  const live = await withPages.tryAgent(SLUG, "AVAILABILITY");
  assert.equal(live.status, 200);
  assert.equal(JSON.parse(live.request).intent, "AVAILABILITY");
  assert.equal(JSON.parse(live.response).status, 200);
  const hidden = createMockManagementClient(
    { ...data, merchant: { ...data.merchant, status: "building" } },
    { latencyMs: 0, liveBookingDelayMs: 0 },
  );
  assert.equal((await hidden.tryAgent(SLUG, "BOOK")).status, 404);
  assert.equal((await hidden.tryAgent(SLUG, "PING")).status, 200);
});

test("the dashboard shows tonight's room and the 30 days ending today", async () => {
  const mock = client();
  const dashboard = await mock.dashboard(SLUG);

  assert.equal(dashboard.date, TODAY);
  assert.equal(dashboard.history.length, 30);
  assert.equal(dashboard.history.at(-1)?.date, TODAY);
  assert.deepEqual(dashboard.next_days.map((day) => day.date).slice(0, 2), [TODAY, TOMORROW]);
  assert.deepEqual(dashboard.room.service, { start: "18:30", end: "21:30" });
  assert.equal(dashboard.room.guests, 17);
  assert.equal(dashboard.room.seats, 24);
  assert.equal(dashboard.room.tables.filter((table) => table.booked).length, 6);
  assert.equal(dashboard.room.largest_group, 4);
  assert.equal(dashboard.room.party_size, 4);
  assert.equal(dashboard.room.next_free, null); // T7, T8 and T9 are taken until the last seating
  assert.ok(dashboard.activity.length <= 6);
  const merchant = await mock.merchant(SLUG);
  assert.equal(merchant.now.slice(0, 10), dashboard.date); // the page's today
  assert.match(merchant.now, /^2026-10-31T19:40:\d{2}\+01:00$/);
});

test("a day lists every start of the grid, past ones included", async () => {
  const day = await client().reservations(SLUG, TODAY);

  assert.equal(day.starts[0], "11:30");
  assert.equal(day.starts.at(-1), "20:30");
  assert.equal(day.starts.length, 10);
});

test("slots never offer a time that has passed, flag blocked ones and can leave a booking out", async () => {
  const mock = client();
  const tonight = await mock.slots(SLUG, { date: TODAY, partySize: 2 });
  const tomorrow = await mock.slots(SLUG, { date: TOMORROW, partySize: 2 });
  const ignoring = await mock.slots(SLUG, { date: TOMORROW, partySize: 2, ignore: "R-021" });

  assert.equal(tonight.slots[0]?.time, "20:00");
  assert.deepEqual(
    tomorrow.slots.filter((slot) => slot.blocked).map((slot) => slot.time),
    ["13:00"],
  );
  const at = (slots: typeof tomorrow.slots, time: string) => slots.find((s) => s.time === time);
  assert.equal(at(tomorrow.slots, "20:00")?.until, "22:00");
  assert.equal(at(tomorrow.slots, "20:00")?.table?.label, "T4"); // T1 to T3 hold Nora Blanc, Alice Laurent, Zoé Huber
  assert.equal(at(ignoring.slots, "20:00")?.table?.label, "T1");
});

test("adding a booking follows the server's rules, and a replay changes nothing", async () => {
  const mock = client();
  const booking = {
    starts_at: `${TODAY}T20:30:00+01:00`,
    party_size: 2,
    guest_name: "  Ines Roux ",
    idempotency_key: "web-1",
  };

  const past = await refusal(mock.addBooking(SLUG, { ...booking, starts_at: `${TODAY}T19:00:00+01:00` }));
  assert.equal(past.body.error, "slot_unavailable");
  const naive = await refusal(mock.addBooking(SLUG, { ...booking, starts_at: `${TODAY}T20:30:00` }));
  assert.equal(naive.body.error, "invalid_input");
  const blocked = await refusal(
    mock.addBooking(SLUG, { ...booking, starts_at: `${TOMORROW}T13:00:00+01:00` }),
  );
  assert.equal(blocked.body.error, "slot_unavailable");
  const large = await refusal(mock.addBooking(SLUG, { ...booking, party_size: 5 }));
  assert.equal(large.body.error, "party_too_large");
  assert.equal(large.body.fallback_url, data.rules.fallback_url);
  const extra = { ...booking, via: "website" } as typeof booking;
  assert.equal((await refusal(mock.addBooking(SLUG, extra))).body.error, "invalid_input");

  const farAway = { ...booking, starts_at: "2100-01-01T20:00:00+01:00" };
  assert.equal((await refusal(mock.addBooking(SLUG, farAway))).body.error, "invalid_input");

  const added = await mock.addBooking(SLUG, booking);
  const replayed = await mock.addBooking(SLUG, booking);
  const sameInstant = await mock.addBooking(SLUG, { ...booking, starts_at: `${TODAY}T19:30:00Z` });
  assert.deepEqual(sameInstant, added);
  const reused = await refusal(mock.addBooking(SLUG, { ...booking, party_size: 3 }));

  assert.deepEqual(replayed, added);
  assert.equal(reused.body.error, "invalid_input");
  assert.equal(added.guest_name, "Ines Roux");
  assert.equal(added.via, "merchant");
  assert.equal(added.id, "R-057"); // R-056 is the live booking's
  const day = await mock.reservations(SLUG, TODAY);
  assert.equal(day.reservations.filter((b) => b.guest_name === "Ines Roux").length, 1);
  const dashboard = await mock.dashboard(SLUG);
  assert.equal(dashboard.activity[0]?.kind, "new_booking");
  assert.equal(dashboard.activity[0]?.source, "merchant");
});

test("a move keeps the day and the future, and a cancellation says whether the guest is told", async () => {
  const mock = client();
  const move = (id: string, starts_at: string) =>
    mock.moveBooking(SLUG, id, { starts_at, idempotency_key: `move-${id}` });

  assert.equal((await move("R-023", `${TOMORROW}T20:30:00+01:00`)).time, "20:30");
  assert.equal((await refusal(move("R-023", `${TODAY}T21:00:00+01:00`))).body.error, "invalid_input");
  assert.equal((await refusal(move("R-001", `${TODAY}T20:30:00+01:00`))).body.error, "slot_unavailable");

  assert.equal((await mock.cancelBooking(SLUG, "R-023")).outcome, "notified");
  assert.equal((await mock.cancelBooking(SLUG, "R-023")).outcome, "already_cancelled");
  assert.equal((await mock.cancelBooking(SLUG, "R-016")).outcome, "own_booking");
  assert.equal((await mock.cancelBooking(SLUG, "R-055")).outcome, "guest_cancelled");
  assert.equal((await mock.cancelBooking(SLUG, "R-001")).outcome, "ended");
  assert.equal((await refusal(mock.cancelBooking(SLUG, "R-999"))).status, 404);
});

test("each form rule answers the problem the web words", async () => {
  const mock = client();
  const { tables } = await mock.tables(SLUG);
  const hours = (services: { start?: string; end?: string }[]) => mock.saveHours(SLUG, { services });
  const plan = (rows: { label: string; seats: number }[]) => mock.saveTables(SLUG, { tables: rows });

  assert.deepEqual(await problemOf(hours([])), { code: "no_services" });
  assert.deepEqual(await problemOf(hours([{ start: "18:30" }])), {
    code: "service_incomplete",
    services: [0],
  });
  assert.deepEqual(await problemOf(hours([{ start: "18:30", end: "" }])), {
    code: "service_incomplete",
    services: [0],
  }); // a cleared time input
  assert.deepEqual(await problemOf(hours([{ start: "18:30", end: "19:00" }])), {
    code: "service_too_short",
    services: [0],
  });
  assert.deepEqual(
    await problemOf(hours([{ start: "18:30", end: "21:30" }, { start: "11:30", end: "19:00" }])),
    { code: "services_overlap", services: [0, 1] },
  );
  assert.deepEqual(await problemOf(plan([])), { code: "no_tables" });
  assert.deepEqual(await problemOf(plan([...tables, { label: "T1", seats: 2 }])), {
    code: "duplicate_table",
    table: "T1",
  });
  assert.deepEqual(await problemOf(plan([...tables, { label: "T10", seats: 0 }])), {
    code: "table_without_seats",
    table: "T10",
  });
  assert.deepEqual(await problemOf(plan([...tables, { label: "T10", seats: 1001 }])), {
    code: "too_many_seats",
    table: "T10",
    seats: 1000,
  });
  assert.deepEqual(await problemOf(plan(tables.filter((t) => t.label !== "T9"))), {
    code: "table_in_use",
    table: "T9",
    guest: "Marie Lang",
  });
  const shrunk = await problemOf(plan(tables.map((t) => (t.label === "T8" ? { ...t, seats: 2 } : t))));
  assert.equal(shrunk?.code, "table_too_small");
  assert.deepEqual(await problemOf(mock.saveRules(SLUG, { ...RULES, max_party_size: 0 })), {
    code: "group_too_small",
  });
  assert.deepEqual(
    await problemOf(mock.saveRules(SLUG, { ...RULES, fallback_url: "https://elsewhere.example/" })),
    { code: "booking_page_off_site" },
  );
  assert.deepEqual(await problemOf(mock.saveSettings(SLUG, { ...SETTINGS, name: " " })), {
    code: "name_required",
  });
});

test("a floor plan names the first booking it strands by start, a running one included", async () => {
  const mock = client();
  const { tables } = await mock.tables(SLUG);
  const plan = (removed: string[], shrunk: Record<string, number> = {}) =>
    problemOf(
      mock.saveTables(SLUG, {
        tables: tables
          .filter((t) => !removed.includes(t.label))
          .map((t) => ({ ...t, seats: shrunk[t.label] ?? t.seats })),
      }),
    );
  const tooSmall = { code: "table_too_small", table: "T7", guest: "Theo Bauer", seats: 4 };

  assert.deepEqual(await plan([], { T7: 3 }), tooSmall); // 19:00 to 21:00, under way
  assert.deepEqual(await plan(["T2"], { T7: 3 }), tooSmall); // before R-010, at 20:00
  assert.deepEqual(await plan(["T5", "T6"]), {
    code: "table_in_use",
    table: "T6",
    guest: "Eli Dupont",
  }); // T5 only has a past and a cancelled booking
  assert.deepEqual(await plan(["T5"]), { code: "table_in_use", table: "T5", guest: "Anna Klein" });
});

test("the booking page stays on the site: host without www., port by default the scheme's", async () => {
  const page = (fallback_url: string, website = data.merchant.website) =>
    clientOf({ merchant: { ...data.merchant, website } }).saveRules(SLUG, { ...RULES, fallback_url });
  const offSite = { code: "booking_page_off_site" };

  const www = "http://www.localhost:8081/#reserver";
  assert.equal((await page(www)).fallback_url, www);
  assert.ok(await page("https://localhost:8081/"));
  assert.deepEqual(await problemOf(page("http://localhost/")), offSite);
  assert.ok(await page("http://zinc.example:443/reserver", "https://zinc.example/"));
  assert.deepEqual(await problemOf(page("http://zinc.example/", "https://zinc.example/")), offSite);
});

test("an identical save writes no activity line", async () => {
  const mock = client();
  const before = (await mock.dashboard(SLUG)).activity;

  await mock.saveHours(SLUG, { services: data.hours.services });
  await mock.saveTables(SLUG, { tables: data.tables.toReversed() });
  await mock.saveRules(SLUG, RULES);
  await mock.saveSettings(SLUG, SETTINGS);

  assert.deepEqual((await mock.dashboard(SLUG)).activity, before);
});

test("the same hours in another order are no change", async () => {
  const mock = client();
  const before = (await mock.dashboard(SLUG)).activity;

  const saved = await mock.saveHours(SLUG, { services: data.hours.services.toReversed() });
  await mock.saveHours(SLUG, { services: data.hours.services });

  assert.deepEqual(saved.services, data.hours.services);
  assert.deepEqual((await mock.dashboard(SLUG)).activity, before);
});

test("a form's malformed field is refused before any of its rules", async () => {
  const mock = client();
  const generic = async (promise: Promise<unknown>) => {
    const { status, body } = await refusal(promise);
    assert.deepEqual([status, body.error, body.problem], [422, "invalid_input", undefined]);
  };
  const hours = (services: { start?: string; end?: string }[]) => mock.saveHours(SLUG, { services });
  const t1 = { label: "T1", seats: 2 };

  await generic(hours([{ start: "18:30" }, { start: "25:00", end: "26:00" }]));
  assert.deepEqual(await problemOf(hours([{ start: "18:30", end: " " }])), {
    code: "service_incomplete",
    services: [0],
  });
  await generic(mock.saveTables(SLUG, { tables: [t1, t1, { label: "T".repeat(21), seats: 2 }] }));
  await generic(mock.saveTables(SLUG, { tables: [...data.tables, { label: "T\u00a010", seats: 2 }] }));
  await generic(mock.saveRules(SLUG, { ...RULES, max_party_size: 0, fallback_url: "not a url" }));
  const price = "€€€€€" as typeof SETTINGS.price_level;
  await generic(mock.saveSettings(SLUG, { ...SETTINGS, name: " ", price_level: price }));
  await generic(mock.saveSettings(SLUG, { ...SETTINGS, tags: Array(8).fill("vegan") }));
});

test("forms refuse what the platform's models refuse", async () => {
  const mock = client();

  // What GET /rules answered is not a RulesInput: the platform forbids extra fields.
  const answered = { ...data.rules } as typeof RULES;
  assert.equal((await refusal(mock.saveRules(SLUG, answered))).body.error, "invalid_input");
  const badUrl = await refusal(mock.saveRules(SLUG, { ...RULES, fallback_url: "not a url" }));
  assert.equal(badUrl.body.error, "invalid_input");
  const ftp = await refusal(mock.saveRules(SLUG, { ...RULES, fallback_url: "ftp://localhost:8081/" }));
  assert.equal(ftp.body.error, "invalid_input");
  const bare = await mock.saveRules(SLUG, { ...RULES, fallback_url: "http://localhost:8081" });
  assert.equal(bare.fallback_url, "http://localhost:8081/");
  const policy = { ...RULES, cancellation_policy: "Two\nlines" };
  assert.equal((await refusal(mock.saveRules(SLUG, policy))).body.error, "invalid_input");
  const longName = { ...SETTINGS, name: "N".repeat(81) };
  assert.equal((await refusal(mock.saveSettings(SLUG, longName))).body.error, "invalid_input");
  assert.equal((await mock.saveSettings(SLUG, { ...SETTINGS, city: "  " })).city, null);
});

test("saving rules, hours or settings rewrites what an AI agent reads", async () => {
  const mock = client();

  await mock.saveRules(SLUG, { ...RULES, max_party_size: 2 });
  await mock.saveSettings(SLUG, { ...SETTINGS, name: "La Graine" });
  const rules = await mock.saveRules(SLUG, { ...RULES, max_party_size: 2, cancellation_policy: "" });

  assert.equal(rules.cancellation_policy, null);
  assert.equal(
    rules.tools[0]?.description,
    "Check free tables at La Graine (Mulhouse). Services: 11:30-14:30, 18:30-21:30. Groups up to 2.",
  );
  assert.match(rules.tools[3]?.description ?? "", /Policy: see the restaurant's own website\.$/);
  assert.equal((await mock.dashboard(SLUG)).room.largest_group, 2);
});

test("blocking sets the day's whole set, with one activity line per save", async () => {
  const mock = client();

  const { blocked } = await mock.setBlocked(SLUG, TOMORROW, ["19:00", "19:30"]);
  await mock.setBlocked(SLUG, TOMORROW, ["19:00", "19:30"]); // no change, no line

  assert.deepEqual(blocked.map((b) => [b.date, b.time]), [
    [TOMORROW, "19:00"],
    [TOMORROW, "19:30"],
  ]);
  const activity = (await mock.dashboard(SLUG)).activity;
  assert.deepEqual([activity[0]?.kind, activity[0]?.text], ["blocked", "tomorrow 19:00, 19:30"]);
  assert.equal(activity.filter((a) => a.kind === "blocked").length, 2); // with the fixture's
  await mock.setBlocked(SLUG, TOMORROW, []);
  assert.equal((await mock.dashboard(SLUG)).activity[0]?.kind, "reopened");
});

test("blocking a time that isn't a start of the day changes nothing", async () => {
  const mock = client();

  const error = await refusal(mock.setBlocked(SLUG, TOMORROW, ["19:00", "21:30"]));

  assert.deepEqual([error.status, error.body.error], [422, "invalid_input"]);
  assert.match(error.body.message, /^21:30 is not a start time on 2026-11-01; valid times: 11:30, 12:00, /);
  assert.deepEqual((await mock.reservations(SLUG, TOMORROW)).blocked, ["13:00"]);
});

test("a blocked-times save keeps the day's past blocks and ignores past times sent", async () => {
  const past = { starts_at: `${TODAY}T19:00:00+01:00`, date: TODAY, time: "19:00" };
  const mock = clientOf({ blocked: [...data.blocked, past] });
  const line = async () => {
    const [item] = (await mock.dashboard(SLUG)).activity;
    return [item?.kind, item?.text];
  };

  const { blocked } = await mock.setBlocked(SLUG, TODAY, ["19:30", "20:30"]);

  assert.deepEqual(blocked.map((b) => b.time), ["20:30", "13:00"]);
  assert.deepEqual((await mock.reservations(SLUG, TODAY)).blocked, ["19:00", "20:30"]);
  assert.deepEqual(await line(), ["blocked", "today 20:30"]);
  await mock.setBlocked(SLUG, TODAY, []);
  assert.deepEqual((await mock.reservations(SLUG, TODAY)).blocked, ["19:00"]);
  assert.deepEqual(await line(), ["reopened", "today 20:30"]);
});

test("guests are grouped by name, and search finds a booking by reference", async () => {
  const mock = client();

  const { guests } = await mock.guests(SLUG, "lena");
  const { reservations } = await mock.search(SLUG, "r-007");

  assert.deepEqual(
    guests.map((g) => [g.guest_name, g.bookings, g.reservation_id]),
    [["Lena Becker", 2, "R-049"]], // Lena Laurent cancelled hers
  );
  assert.deepEqual((await mock.guests(SLUG, "  LÉNA  bëcker")).guests, guests);
  assert.deepEqual(reservations.map((b) => b.guest_name), ["Anna Huber"]);
  assert.deepEqual((await mock.search(SLUG, "")).reservations, []);
});

test("the live booking arrives once, whoever listens, and shows on the dashboard", async () => {
  const mock = client(10);
  const events: LiveEvent[][] = [[], []];
  const before = await mock.dashboard(SLUG);

  await new Promise<void>((resolve) => {
    const handlers = (index: number) => ({
      onEvent: (event: LiveEvent) => {
        events[index]?.push(event);
        if (index === 1) resolve();
      },
      onConnection: () => {},
    });
    mock.subscribe(SLUG, handlers(0));
    mock.subscribe(SLUG, handlers(1));
  });
  const after = await mock.dashboard(SLUG);

  assert.deepEqual(events, [[data.live_booking], [data.live_booking]]);
  assert.equal(after.room.guests, before.room.guests + 2);
  assert.equal(after.history.at(-1)?.agent_guests, (before.history.at(-1)?.agent_guests ?? 0) + 2);
  const agent = (d: typeof after) => d.channels.find((c) => c.via === "claude")?.bookings;
  assert.equal(agent(after), (agent(before) ?? 0) + 1);
  const day = await mock.reservations(SLUG, TODAY);
  assert.equal(day.reservations.filter((b) => b.id === data.live_booking.reservation.id).length, 1);
});

test("the live booking keeps its own table when it is free", async () => {
  const mock = client(10);
  await mock.cancelBooking(SLUG, "R-010"); // frees T2 at 20:00, before T4

  const event = await new Promise<LiveEvent>((resolve) => {
    mock.subscribe(SLUG, { onEvent: resolve, onConnection: () => {} });
  });

  assert.equal(event.reservation.table_label, data.live_booking.reservation.table_label);
});

test("the live booking takes another table if the merchant took its own", async () => {
  const mock = client(10);
  await mock.addBooking(SLUG, {
    starts_at: `${TODAY}T20:00:00+01:00`,
    party_size: 2,
    guest_name: "Walk-in",
    idempotency_key: "walk-in",
  });

  const event = await new Promise<LiveEvent>((resolve) => {
    mock.subscribe(SLUG, { onEvent: resolve, onConnection: () => {} });
  });

  assert.equal((await mock.reservations(SLUG, TODAY)).reservations.find((b) => b.guest_name === "Walk-in")?.table_label, "T4");
  assert.equal(event.reservation.table_label, "T5");
});

test("pausing the agent is the merchant's status, and requests filter by refusal", async () => {
  const mock = client();

  assert.equal((await mock.pauseAgent(SLUG)).status, "down");
  assert.equal((await mock.merchant(SLUG)).status, "down");
  // Like the server: publishing doesn't silently undo a pause.
  await assert.rejects(mock.publish(SLUG), /resume it rather than publish it again/);
  assert.equal((await mock.merchant(SLUG)).status, "down");
  assert.equal((await mock.resumeAgent(SLUG)).status, "live");
  const refused = await mock.agentRequests(SLUG, { date: TODAY, refused: true });
  const all = await mock.agentRequests(SLUG, { date: TODAY, refused: false });

  assert.deepEqual(refused.requests.map((r) => r.status), [409, 413]);
  assert.equal(all.requests.length, 10); // the latest of the day's 24
});
