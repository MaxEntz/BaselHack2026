import { describe, expect, it } from "vitest";
import type { Problem } from "../types.ts";
import { ManagementRequestError } from "./client.ts";
import { errorMessage, problemMessage } from "./problems.ts";
import { lastSeating, serviceNames, startsOf } from "./services.ts";

describe("services", () => {
  it("names them by start time", () => {
    const at = (start: string) => ({ start, end: "23:00" });
    expect(serviceNames([at("08:00"), at("11:30"), at("12:00"), at("18:30")])).toEqual([
      "Breakfast",
      "Lunch",
      "Lunch 2",
      "Dinner",
    ]);
  });

  it("seats every 30 minutes up to an hour before the end", () => {
    expect(startsOf({ start: "11:30", end: "14:30" })).toEqual(["11:30", "12:00", "12:30", "13:00", "13:30"]);
    expect(lastSeating({ start: "18:30", end: "19:00" })).toBeNull();
    expect(lastSeating({ start: "18:30", end: "" })).toBeNull();
    expect(startsOf({ start: "18:30", end: "19:00" })).toEqual([]);
  });
});

describe("form problems", () => {
  const names = ["Lunch", "Dinner"];
  it.each<[Problem, string]>([
    [{ code: "no_services" }, "Keep at least one service."],
    [{ code: "service_incomplete", services: [0] }, "Give Lunch a start and an end."],
    [{ code: "service_too_short", services: [1] }, "Dinner has to last at least an hour."],
    [{ code: "services_overlap", services: [0, 1] }, "Lunch and Dinner overlap."],
    [{ code: "no_tables" }, "Keep at least one table."],
    [{ code: "duplicate_table", table: "T1" }, "There are two tables called T1."],
    [{ code: "table_without_seats", table: "T1" }, "Every table needs at least 1 seat."],
    [{ code: "too_many_seats", table: "T1", seats: 1000 }, "T1 can have at most 1000 seats."],
    [{ code: "too_many_seats", seats: 1000 }, "The largest group can be at most 1000."],
    [
      { code: "table_in_use", table: "T4", guest: "Kim Weber" },
      "T4 has to stay, because Kim Weber's booking refers to it.",
    ],
    [
      { code: "table_too_small", table: "T4", guest: "Kim Weber", seats: 4 },
      "T4 needs 4 seats, because of Kim Weber's booking.",
    ],
    [{ code: "group_too_small" }, "The largest group has to be at least 1."],
    [{ code: "name_required" }, "Your restaurant needs a name."],
    [{ code: "booking_page_off_site" }, "Your booking page has to be on your own website."],
  ])("%j", (problem, message) => {
    expect(problemMessage(problem, names)).toBe(message);
  });

  it("falls back to the platform's message", () => {
    const refused = new ManagementRequestError(501, {
      error: "not_built_yet",
      message: "Not built yet: see #133.",
    });
    expect(errorMessage(refused)).toBe("Not built yet: see #133.");
  });
});
