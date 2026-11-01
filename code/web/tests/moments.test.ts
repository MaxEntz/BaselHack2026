// The moments of the day (#154), with Node's own runner (`npm test`).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MOMENT_READY,
  askedMoment,
  isMoment,
  isMomentReady,
  momentAt,
  momentSentence,
  previewTime,
  shownMoment,
} from "../src/moments.ts";

const LUNCH = { start: "11:30", end: "14:30" };
const DINNER = { start: "18:30", end: "21:30" };
const DAY = [DINNER, LUNCH];

const at = (time: string) => {
  const found = momentAt(time, DAY);
  return found && [found.moment, found.service.start];
};

test("the clock picks the moment and the service it is about", () => {
  assert.deepEqual(at("09:00"), ["morning", "18:30"]);
  assert.deepEqual(at("10:29"), ["morning", "18:30"]);
  assert.deepEqual(at("10:30"), ["before", "11:30"]);
  assert.deepEqual(at("11:30"), ["during", "11:30"]);
  assert.deepEqual(at("14:29"), ["during", "11:30"]);
  assert.deepEqual(at("14:30"), ["morning", "18:30"]);
  assert.deepEqual(at("17:29"), ["morning", "18:30"]);
  assert.deepEqual(at("17:30"), ["before", "18:30"]);
  assert.deepEqual(at("20:00"), ["during", "18:30"]);
  assert.deepEqual(at("21:30"), ["after", "18:30"]);
  assert.deepEqual(at("23:59"), ["after", "18:30"]);
});

test("a day without a service has no moment", () => {
  assert.equal(momentAt("12:00", []), null);
  assert.equal(previewTime("during", []), null);
});

test("each moment is previewed at its time, on the last service", () => {
  assert.equal(previewTime("morning", DAY), "10:00");
  assert.equal(previewTime("before", DAY), "17:30");
  assert.equal(previewTime("during", DAY), "19:30");
  assert.equal(previewTime("after", DAY), "22:30");
  assert.equal(previewTime("after", [{ start: "20:00", end: "23:30" }]), "23:59");
});

test("a preview wins over the clock, and shows its own time", () => {
  assert.deepEqual(shownMoment("during", "09:00", DAY), { moment: "during", service: DINNER, time: "19:30" });
  assert.deepEqual(shownMoment(null, "09:00", DAY), { moment: "morning", service: DINNER, time: "09:00" });
});

test("Studio says what each moment shows", () => {
  assert.equal(
    momentSentence("during", DAY),
    "At 19:30 it shows the next arrival in type you can read from across the room.",
  );
  assert.equal(momentSentence("after", []), "It shows how the service went.");
});

test("only the four moments are moments", () => {
  assert.equal(isMoment("during"), true);
  assert.equal(isMoment("noon"), false);
  assert.equal(isMoment(null), false);
});

test("the management site reads a moment only from Studio's message (#220)", () => {
  assert.equal(askedMoment({ type: "sesame:moment", moment: "after" }), "after");
  assert.equal(askedMoment({ type: "sesame:moment", moment: null }), null);
  assert.equal(askedMoment({ type: "sesame:moment", moment: "noon" }), undefined);
  assert.equal(askedMoment({ type: "sesame:moment" }), undefined);
  assert.equal(askedMoment({ type: "other", moment: "after" }), undefined);
  assert.equal(askedMoment("sesame:moment"), undefined);
  assert.equal(askedMoment(null), undefined);
});

test("Studio reads the frame's page as listening only from its own message (#220)", () => {
  assert.equal(isMomentReady(MOMENT_READY), true);
  assert.equal(isMomentReady({ type: "sesame:moment-ready" }), true);
  assert.equal(isMomentReady({ type: "sesame:moment", moment: null }), false);
  assert.equal(isMomentReady("sesame:moment-ready"), false);
  assert.equal(isMomentReady(null), false);
});
