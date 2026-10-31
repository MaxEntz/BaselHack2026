// The management mock client (#129) against the demo fixture: it applies the
// server's rules, so pages built on it behave like the real site.
import assert from "node:assert/strict";
import { test } from "node:test";

import fixture from "../../tests/fixtures/demo/la-graine-verte-management.json" with { type: "json" };
import { ManagementRequestError } from "../src/management/client.ts";
import { createMockManagementClient } from "../src/mocks/managementClient.ts";
import type { LiveEvent, ManagementFixture } from "../src/types.ts";

const SLUG = "la-graine-verte";
const TODAY = "2026-10-31";
const TOMORROW = "2026-11-01";
const data = fixture as ManagementFixture;
const client = () =>
  createMockManagementClient(data, { latencyMs: 0, liveBookingDelayMs: 0 });

async function refusal(promise: Promise<unknown>): Promise<ManagementRequestError> {
  const error = await promise.then(
    () => assert.fail("expected a refusal"),
    (reason: unknown) => reason,
  );
  assert.ok(error instanceof ManagementRequestError);
  return error;
}

test("the dashboard shows tonight's room and the 30 days ending today", async () => {
  const dashboard = await client().dashboard(SLUG, TODAY);

  assert.equal(dashboard.date, TODAY);
  assert.equal(dashboard.history.length, 30);
  assert.equal(dashboard.history.at(-1)?.date, TODAY);
  assert.deepEqual(dashboard.next_days.map((day) => day.date).slice(0, 2), [TODAY, TOMORROW]);
  assert.deepEqual(dashboard.room.service, { start: "18:30", end: "21:30" });
  assert.equal(dashboard.room.guests, 17);
  assert.equal(dashboard.room.seats, 24);
  assert.equal(dashboard.room.tables.filter((table) => table.booked).length, 6);
  assert.equal(dashboard.room.largest_group, 4);
  assert.ok(dashboard.activity.length <= 6);
});

test("slots never offer a time that has passed and flag blocked ones", async () => {
  const mock = client();
  const tonight = await mock.slots(SLUG, { date: TODAY, partySize: 2 });
  const tomorrow = await mock.slots(SLUG, { date: TOMORROW, partySize: 2 });

  assert.equal(tonight.slots[0]?.time, "20:00");
  assert.deepEqual(
    tomorrow.slots.filter((slot) => slot.blocked).map((slot) => slot.time),
    ["13:00"],
  );
  const at = (time: string) => tomorrow.slots.find((slot) => slot.time === time);
  assert.equal(at("20:00")?.until, "22:00");
  assert.equal(at("20:00")?.table?.label, "T2"); // T1 holds Clara Meyer
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
  const large = await refusal(mock.addBooking(SLUG, { ...booking, party_size: 5 }));
  assert.equal(large.body.error, "party_too_large");
  assert.equal(large.body.fallback_url, data.rules.fallback_url);

  const added = await mock.addBooking(SLUG, booking);
  const replayed = await mock.addBooking(SLUG, booking);

  assert.deepEqual(replayed, added);
  assert.equal(added.guest_name, "Ines Roux");
  assert.equal(added.via, "merchant");
  const day = await mock.reservations(SLUG, TODAY);
  assert.equal(day.reservations.filter((b) => b.guest_name === "Ines Roux").length, 1);
  const dashboard = await mock.dashboard(SLUG, TODAY);
  assert.equal(dashboard.activity[0]?.kind, "new_booking");
  assert.equal(dashboard.activity[0]?.source, "merchant");
});

test("a move keeps the day, and a cancellation says whether the guest is told", async () => {
  const mock = client();

  const moved = await mock.moveBooking(SLUG, "R-012", {
    starts_at: `${TOMORROW}T20:30:00+01:00`,
    idempotency_key: "web-2",
  });
  assert.equal(moved.time, "20:30");
  const otherDay = await refusal(
    mock.moveBooking(SLUG, "R-012", { starts_at: `${TODAY}T21:00:00+01:00`, idempotency_key: "web-3" }),
  );
  assert.equal(otherDay.body.error, "invalid_input");

  assert.equal((await mock.cancelBooking(SLUG, "R-012")).outcome, "notified");
  assert.equal((await mock.cancelBooking(SLUG, "R-012")).outcome, "already_cancelled");
  assert.equal((await mock.cancelBooking(SLUG, "R-016")).outcome, "own_booking");
  assert.equal((await mock.cancelBooking(SLUG, "R-011")).outcome, "guest_cancelled");
  assert.equal((await mock.cancelBooking(SLUG, "R-001")).outcome, "ended");
  assert.equal((await refusal(mock.cancelBooking(SLUG, "R-999"))).status, 404);
});

test("saving forms answers the problem the web words", async () => {
  const mock = client();
  const { tables } = await mock.tables(SLUG);

  const removed = await refusal(mock.saveTables(SLUG, tables.filter((t) => t.label !== "T9")));
  assert.deepEqual(removed.body.problem, { code: "table_in_use", table: "T9", guest: "Emma Roth" });
  const shrunk = await refusal(
    mock.saveTables(SLUG, tables.map((t) => (t.label === "T8" ? { ...t, seats: 2 } : t))),
  );
  assert.equal(shrunk.body.problem?.code, "table_too_small");
  const overlap = await refusal(
    mock.saveHours(SLUG, { services: [{ start: "11:30", end: "14:30" }, { start: "14:00", end: "16:00" }] }),
  );
  assert.deepEqual(overlap.body.problem, { code: "services_overlap", services: [0, 1] });
  const nameless = await refusal(
    mock.saveSettings(SLUG, { name: " ", price_level: "€", tags: [] }),
  );
  assert.equal(nameless.body.problem?.code, "name_required");

  const saved = await mock.saveRules(SLUG, { ...data.rules, max_party_size: 2 });
  assert.equal(saved.max_party_size, 2);
  assert.equal((await mock.dashboard(SLUG, TODAY)).room.largest_group, 2);
});

test("blocking sets the day's whole set and logs each change", async () => {
  const mock = client();

  const { blocked } = await mock.setBlocked(SLUG, TOMORROW, ["19:00"]);

  assert.deepEqual(blocked.map((b) => [b.date, b.time]), [[TOMORROW, "19:00"]]);
  const kinds = (await mock.dashboard(SLUG, TODAY)).activity.map((item) => item.kind);
  assert.deepEqual(kinds.slice(0, 2), ["reopened", "blocked"]);
});

test("guests are grouped by name, and search finds a booking by reference", async () => {
  const mock = client();

  const { guests } = await mock.guests(SLUG, "lena");
  const { reservations } = await mock.search(SLUG, "r-007");

  assert.deepEqual(
    guests.map((g) => [g.guest_name, g.bookings, g.reservation_id]),
    [["Lena Becker", 1, "R-010"]], // her 19:00 booking tonight has started
  );
  assert.deepEqual(reservations.map((b) => b.guest_name), ["Emma Roth"]);
  assert.deepEqual((await mock.search(SLUG, "")).reservations, []);
});

test("the live booking arrives once and shows on the dashboard", async () => {
  const mock = client();
  const events: LiveEvent[] = [];
  const before = await mock.dashboard(SLUG, TODAY);

  await new Promise<void>((resolve) => {
    mock.subscribe(SLUG, { onEvent: (event) => (events.push(event), resolve()), onConnection: () => {} });
  });
  const after = await mock.dashboard(SLUG, TODAY);

  assert.deepEqual(events, [data.live_booking]);
  assert.equal(after.room.guests, before.room.guests + 2);
  assert.equal(after.history.at(-1)?.agent_guests, (before.history.at(-1)?.agent_guests ?? 0) + 2);
  const agent = (d: typeof after) => d.channels.find((c) => c.via === "claude")?.bookings;
  assert.equal(agent(after), (agent(before) ?? 0) + 1);
  assert.equal(after.activity[0]?.reservation_id, data.live_booking.reservation.id);
});

test("pausing the agent is the merchant's status, and requests filter by refusal", async () => {
  const mock = client();

  assert.equal((await mock.pauseAgent(SLUG)).status, "down");
  assert.equal((await mock.merchant(SLUG)).status, "down");
  const refused = await mock.agentRequests(SLUG, { date: TODAY, refused: true });
  const all = await mock.agentRequests(SLUG, { date: TODAY, refused: false });

  assert.deepEqual(refused.requests.map((r) => r.status), [409, 413]);
  assert.equal(all.requests.length, 4);
});
