import { describe, expect, it } from "vitest";
import type { DaySlot, ManagedReservation } from "../../../types.ts";
import {
  bookedLine,
  cancelNote,
  columnTitle,
  dayLabel,
  hasEnded,
  hasStarted,
  newBookingNote,
  rowNote,
  summary,
  whenLine,
} from "./words.ts";

const TODAY = "2026-10-31";

function booking(changes: Partial<ManagedReservation> = {}): ManagedReservation {
  return {
    id: "R-014",
    date: TODAY,
    time: "20:00",
    guest_name: "Marie Lang",
    party_size: 4,
    table_label: "T9",
    via: "claude",
    status: "confirmed",
    cancelled_by: null,
    created_at: "2026-10-31T10:05:00+01:00",
    ...changes,
  };
}

function slot(time: string, table: string | null, blocked = false): DaySlot {
  return {
    starts_at: `${TODAY}T${time}:00+01:00`,
    time,
    until: `${String(Number(time.slice(0, 2)) + 2).padStart(2, "0")}${time.slice(2)}`,
    table: table ? { label: table, seats: 2 } : null,
    blocked,
  };
}

describe("the day's label", () => {
  it.each([
    [TODAY, "Dinner", "Tonight"],
    [TODAY, "Lunch", "Today"],
    ["2026-11-01", "Dinner", "Tomorrow"],
    ["2026-11-02", "Lunch", "Monday"],
  ])("%s at %s is %s", (day, service, label) => {
    expect(dayLabel(day, TODAY, service)).toBe(label);
  });

  it("counts the live bookings shown", () => {
    const shown = [booking(), booking({ id: "R-1", party_size: 1 }), booking({ id: "R-2", status: "cancelled" })];
    expect(summary("Tonight", shown)).toBe("Tonight, 2 bookings and 5 guests.");
    expect(summary("Today", [booking({ party_size: 1 })])).toBe("Today, 1 booking and 1 guest.");
  });
});

describe("a column", () => {
  it("counts its live bookings, or says they are all cancelled, and blocked", () => {
    expect(columnTitle([booking(), booking({ party_size: 2 })], false)).toBe("2 bookings, 6 guests");
    expect(columnTitle([booking({ status: "cancelled" })], true)).toBe("All cancelled, blocked");
  });

  it.each([
    [booking(), "Through your agent"],
    [booking({ via: "merchant" }), "By you"],
    [booking({ via: "website" }), "Through your website"],
    [booking({ status: "cancelled", cancelled_by: "guest" }), "Cancelled by the guest"],
    [booking({ status: "cancelled", cancelled_by: "merchant" }), "Cancelled by you"],
  ])("words who booked %#", (row, note) => {
    expect(rowNote(row)).toBe(note);
  });
});

describe("the booking dialog", () => {
  it("says when, and who booked it when", () => {
    expect(whenLine(booking(), 120)).toBe("Saturday 31 October, 20:00 to 22:00");
    expect(whenLine(booking({ time: "23:00" }), 120)).toBe("Saturday 31 October, 23:00 to 01:00");
    expect(bookedLine(booking())).toBe("through your agent, Sat 31 Oct at 10:05");
    expect(bookedLine(booking({ via: "merchant", created_at: "2026-10-28T16:38:00+01:00" }))).toBe(
      "by you, Wed 28 Oct at 16:38",
    );
  });
});

describe("a booking's time", () => {
  it("has started from its start, and ended after its slot, by the merchant's clock", () => {
    expect(hasStarted(booking(), TODAY, "19:59")).toBe(false);
    expect(hasStarted(booking(), TODAY, "20:00")).toBe(true);
    expect(hasStarted(booking({ date: "2026-10-30" }), TODAY, "08:00")).toBe(true);
    expect(hasEnded(booking(), TODAY, "21:59", 120)).toBe(false);
    expect(hasEnded(booking(), TODAY, "22:00", 120)).toBe(true);
    expect(hasEnded(booking({ date: "2026-11-01" }), TODAY, "23:00", 120)).toBe(false);
  });

  it.each([
    [booking(), false, "Marie Lang sees a notice the next time their assistant checks."],
    [booking({ via: "website" }), false, "Marie Lang sees a notice the next time their assistant checks."],
    [booking({ via: "merchant" }), false, "The table goes back on offer straight away."],
    [booking(), true, "It has already ended, so nobody is told."],
  ])("asks before cancelling %#, as the server will act", (row, ended, note) => {
    expect(cancelNote(row, ended)).toBe(note);
  });
});

describe("the new booking's note", () => {
  const slots = [slot("20:00", null), slot("20:30", "T4"), slot("21:00", null, true)];

  it.each([
    // After the last seating the server sends no start at all: nothing is blocked.
    [{ slots: [], time: null, party: 2 }, "No time is left on this day.", false],
    [{ slots: [slot("21:00", "T4", true)], time: null, party: 2 }, "Every time is blocked on this day.", false],
    [{ slots, time: "20:30", party: 0 }, "A booking needs at least one guest.", false],
    [{ slots, time: "20:30", party: 1.5 }, "A booking needs at least one guest.", false],
    [{ slots, time: "20:30", party: 5 }, "Your limit is 4. You can change it in the booking rules.", false],
    [{ slots, time: "20:30", party: 2 }, "Table T4, 2 seats, until 22:30.", true],
    [{ slots, time: "20:00", party: 2 }, "No table for 2 at 20:00. The closest free time is 20:30.", false],
    [{ slots: [slot("20:00", null)], time: "20:00", party: 2 }, "No table for 2 is free on this day.", false],
  ])("%j: %s", (input, text, ok) => {
    expect(newBookingNote({ ...input, limit: 4 })).toEqual({ text, ok });
  });

  it("waits for the day's times", () => {
    expect(newBookingNote({ slots: null, time: null, party: 2, limit: 4 })).toEqual({ text: "", ok: false });
  });
});
