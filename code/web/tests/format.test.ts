// The cards' wording, on the helpers the management site words its days and counts with (#220).
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { describeDate, describeDay, plural } from "../src/cards/format.ts";
import { DEMO_NOW, setClock } from "../src/clock.ts";
import { dayShort } from "../src/management/dates.ts";
import { bookings, guests } from "../src/management/pages/home/format.ts";

afterEach(() => setClock(null));

test("a count is worded once, thousands separated", () => {
  assert.equal(plural(1, "guest"), "1 guest");
  assert.equal(plural(0, "guest"), "0 guests");
  assert.equal(plural(1204, "guest"), "1,204 guests");
  assert.equal(guests(1204), "1,204 guests");
  assert.equal(bookings(1), "1 booking");
});

test("a booking's day is today, tomorrow, or the management site's short day", () => {
  setClock(DEMO_NOW);
  assert.equal(describeDay("2026-10-31"), "today");
  assert.equal(describeDay("2026-11-01"), "tomorrow");
  assert.equal(describeDay("2026-11-07"), dayShort("2026-11-07"));
  assert.equal(dayShort("2026-11-07"), "Sat 7 Nov");
  assert.equal(describeDate("2026-10-31"), "tonight");
  assert.equal(describeDate("2026-11-07"), "on Sat 7 Nov");
});
