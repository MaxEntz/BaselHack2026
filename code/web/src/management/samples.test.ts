import { describe, expect, it } from "vitest";
import { sampleBookings } from "./samples.ts";
import { startsOf } from "./services.ts";

const times = (start: string, end: string) =>
  sampleBookings("2026-10-31", { start, end }).map((booking) => booking.time);

describe("sample bookings", () => {
  it("sit at the service's first, middle and last start", () => {
    expect(times("18:30", "21:30")).toEqual(["18:30", "19:30", "20:30"]);
  });

  it("stay within a short service, before its last seating", () => {
    expect(times("12:00", "13:30")).toEqual(["12:00", "12:00", "12:30"]);
    expect(times("12:00", "14:00")).toEqual(["12:00", "12:30", "13:00"]); // Le Petit Zinc's lunch
    expect(times("22:00", "23:59")).toEqual(["22:00", "22:00", "22:30"]);
    for (const [start, end] of [["12:00", "13:30"], ["12:00", "14:00"], ["22:00", "23:59"]]) {
      const starts = startsOf({ start, end });
      expect(times(start, end).every((time) => starts.includes(time))).toBe(true);
    }
  });

  it("are none when the service has no bookable start", () => {
    expect(times("12:00", "12:30")).toEqual([]);
  });
});
