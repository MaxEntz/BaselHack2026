import assert from "node:assert/strict";
import { test } from "node:test";
import { chips, pool } from "../src/management/assistant/chips.ts";

// The demo's clock: Saturday 31 October 2026, 19:40.
const SATURDAY_EVENING = "2026-10-31T19:40:00+01:00";

test("the mockup's chips on the demo's evening, the asked ones left out (#140)", () => {
  assert.deepEqual(chips(SATURDAY_EVENING, new Set(["How busy are we tomorrow?", "Block 13:00 on Sunday"])), [
    "Table for 4 at 20:00 tonight",
    "Bookings for Sunday lunch",
    "What are my opening hours?",
    "Is my agent live?",
  ]);
  assert.equal(chips(SATURDAY_EVENING, new Set()).length, 4);
});

test("a time that has passed is never offered", () => {
  const late = pool("2026-10-31T20:15:00+01:00");
  assert.ok(late.includes("Table for 4 at 20:00 tomorrow"));
  assert.ok(!late.some((chip) => chip.includes("tonight")));

  // Sunday after lunch: Sunday is a week away.
  const sunday = pool("2026-11-01T14:00:00+01:00");
  assert.ok(sunday.includes("Bookings for next Sunday lunch"));
  assert.ok(sunday.includes("Block 13:00 next Sunday"));
  // Sunday morning: still today's.
  assert.ok(pool("2026-11-01T09:00:00+01:00").includes("Block 13:00 on Sunday"));
});

test("every chip asked: none left", () => {
  assert.deepEqual(chips(SATURDAY_EVENING, new Set(pool(SATURDAY_EVENING))), []);
});
