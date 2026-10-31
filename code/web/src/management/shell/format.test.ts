import { describe, expect, it } from "vitest";
import { DEMO_NOW } from "../../clock.ts";
import { brandLine, clockLabel, initials, notificationLine, pageHits, shortcutLabel } from "./format.ts";

describe("shell wording", () => {
  it("writes the clock in the merchant's zone", () => {
    expect(clockLabel(DEMO_NOW, "Europe/Zurich")).toBe("Sat 31 Oct, 19:40");
    expect(clockLabel(new Date("2026-11-01T08:05:00+01:00"), "Europe/Zurich")).toBe("Sun 1 Nov, 08:05");
    expect(clockLabel(DEMO_NOW, "UTC")).toBe("Sat 31 Oct, 18:40");
  });

  it("gives the city and the price", () => {
    expect(brandLine("Mulhouse", "€")).toBe("Mulhouse, €");
    expect(brandLine(null, "€€")).toBe("€€");
  });

  it("takes two initials at most", () => {
    expect(initials("Camille Lang")).toBe("CL");
    expect(initials(" camille ")).toBe("C");
    expect(initials("Léa Anne Dubois")).toBe("LA");
  });

  it("shows ⌘K on Apple devices only", () => {
    expect(shortcutLabel("macOS")).toBe("⌘K");
    expect(shortcutLabel("MacIntel")).toBe("⌘K");
    expect(shortcutLabel("iPhone")).toBe("⌘K");
    expect(shortcutLabel("Windows")).toBe("Ctrl K");
    expect(shortcutLabel("Linux x86_64")).toBe("Ctrl K");
  });

  it("words a notification", () => {
    const line = notificationLine({
      id: 1,
      at: "2026-10-31T19:40:00+01:00",
      kind: "new_booking",
      text: "R-056, Kim Weber, 2 guests, today 20:00",
      source: "claude",
      reservation_id: "R-056",
    });
    expect(line).toBe("19:40, new booking: R-056, Kim Weber, 2 guests, today 20:00");
  });

  it("finds pages by name, in the sidebar's order", () => {
    expect(pageHits("  ")).toEqual([]);
    expect(pageHits("RES")).toEqual(["reservations"]);
    expect(pageHits("o")).toEqual(["home", "reservations", "hours", "floor", "rules"]);
  });
});
