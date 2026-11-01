import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import fixture from "../../../../../tests/fixtures/demo/la-graine-verte-management.json" with { type: "json" };
import { createMockManagementClient } from "../../../mocks/managementClient.ts";
import { ManagePage } from "../../ManagePage.tsx";
import { routesWith } from "../../../router.tsx";
import type { ManagementFixture } from "../../../types.ts";
import { ClientContext } from "../../data.ts";

// The management site drawn at once: the app loads it on its first visit (router.tsx).
const routes = routesWith(<ManagePage />);

// The demo fixture: Saturday 31 October at 19:40, La Graine Verte (lunch 11:30-14:30, dinner
// 18:30-21:30, groups up to 4). The live booking stays away unless a test waits for it.
const SLUG = "la-graine-verte";

function open(query = "", liveBookingDelayMs = 60_000, changes: Partial<ManagementFixture> = {}) {
  const data = { ...(fixture as ManagementFixture), ...changes };
  const client = createMockManagementClient(data, { latencyMs: 0, liveBookingDelayMs });
  const router = createMemoryRouter(routes, { initialEntries: [`/manage/${SLUG}/reservations${query}`] });
  render(
    <ClientContext.Provider value={client}>
      <RouterProvider router={router} />
    </ClientContext.Provider>,
  );
  return { client, router };
}

const click = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));
const dialog = () => screen.findByRole("dialog");
const columns = () =>
  [...document.querySelectorAll(".col")].map((col) => ({
    time: col.querySelector(".time-blob b")?.textContent ?? null,
    title: col.querySelector("h3")?.textContent,
    rows: [...col.querySelectorAll(".who")].map((row) => row.textContent),
  }));
/** A toast's text: its bold line, then the rest. */
const toast = async (title: string) => (await screen.findByText(title)).parentElement?.textContent;

describe("Reservations", () => {
  it("opens on tonight, a column per start time", async () => {
    open();
    expect(await screen.findByText("Tonight, 6 bookings and 17 guests.")).toBeTruthy();
    expect(screen.getByText("Saturday 31 October")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Dinner, 18:30–21:30" }).getAttribute("aria-pressed")).toBe("true");
    expect(columns()).toEqual([
      { time: "18:30", title: "1 booking, 2 guests", rows: ["Sara Muller2, T1Through your agent"] },
      {
        time: "19:00",
        title: "2 bookings, 7 guests",
        rows: ["Claire Blanc3, T8By you", "Theo Bauer4, T7Through your agent"],
      },
      {
        time: "20:00",
        title: "3 bookings, 8 guests",
        rows: ["Ines Schmitt2, T2By you", "Luc Hartmann2, T3By you", "Marie Lang4, T9Through your agent"],
      },
    ]);
    expect(screen.getByRole("button", { name: "Previous day" })).toHaveProperty("disabled", true);
    // No booking came through the website: no filter for it.
    expect(screen.queryByRole("button", { name: "Website" })).toBeNull();
  });

  it("shows lunch, filters by who booked, and moves through the week", async () => {
    open();
    await screen.findByText("Tonight, 6 bookings and 17 guests.");

    click("Lunch, 11:30–14:30");
    expect(screen.getByText("Today, 8 bookings and 22 guests.")).toBeTruthy();
    click("You");
    expect(screen.getByText("Today, 3 bookings and 8 guests.")).toBeTruthy();
    expect(columns().flatMap((col) => col.rows).every((row) => row.endsWith("By you"))).toBe(true);
    click("Everyone");

    click("Next day");
    expect(await screen.findByText("Sunday 1 November")).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/^Tomorrow, /)).toBeTruthy());
    click("Lunch, 11:30–14:30");
    const blocked = columns().find((col) => col.time === "13:00");
    expect(blocked?.title).toMatch(/, blocked$/);
    expect(document.querySelector(".col.blocked .time-blob b")?.textContent).toBe("13:00");
    const cancelled = [...document.querySelectorAll(".who.off")].map((row) => row.textContent);
    expect(cancelled).toEqual([expect.stringMatching(/^Max Martin.*Cancelled by (the guest|you)$/)]);
  });

  it("says when a service has no bookings", async () => {
    open("?date=2026-11-02");
    expect(await screen.findByText(/^Monday, 2 bookings and \d+ guests\.$/)).toBeTruthy();
    click("You");
    expect(screen.getByText("Monday, 0 bookings and 0 guests.")).toBeTruthy();
    expect(screen.getByText("No bookings for this service.")).toBeTruthy();
  });
});

describe("A booking", () => {
  it("shows its facts and moves to another free time", async () => {
    const { client } = open("?date=2026-10-31&booking=R-014");
    const box = within(await dialog());
    expect(box.getByRole("heading", { name: "Marie Lang" })).toBeTruthy();
    const facts = [...document.querySelectorAll(".facts > div")].map((row) => row.textContent);
    expect(facts).toEqual([
      "WhenSaturday 31 October, 20:00 to 22:00",
      "Party4 guests",
      "TableT9",
      "Bookedthrough your agent, Sat 31 Oct at 10:05",
      "ReferenceR-014",
    ]);
    // 20:30 is the only other start still to come; a table for 4 is free there once R-014 moves.
    const select = await box.findByRole<HTMLSelectElement>("combobox", { name: "Move to another time" });
    await waitFor(() => expect(select.value).toBe("20:30"));

    fireEvent.click(box.getByRole("button", { name: "Move" }));

    expect(await toast("Marie Lang moved")).toBe("Marie Lang moved20:30, table T9");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const day = await client.reservations(SLUG, "2026-10-31");
    expect(day.reservations.find((b) => b.id === "R-014")?.time).toBe("20:30");
    await waitFor(() => expect(document.querySelector(".who.fresh")?.textContent).toMatch(/^Marie Lang/));
  });

  it("says when no other time is free", async () => {
    // 20:30, the only other start still to come tonight, is blocked.
    const blocked = { starts_at: "2026-10-31T20:30:00+01:00", date: "2026-10-31", time: "20:30" };
    open("?booking=R-014", 60_000, { blocked: [...(fixture as ManagementFixture).blocked, blocked] });
    const box = within(await dialog());
    const select = await box.findByRole<HTMLSelectElement>("combobox", { name: "Move to another time" });
    await waitFor(() => expect(select.textContent).toBe("No other time is free"));
    expect(select.disabled).toBe(true);
    expect(box.getByRole("button", { name: "Move" })).toHaveProperty("disabled", true);
  });

  it("can't move once it has started", async () => {
    open("?booking=R-009");
    const box = within(await dialog());
    expect(box.getByRole("heading", { name: "Sara Muller" })).toBeTruthy();
    expect(box.queryByRole("combobox")).toBeNull();
    expect(box.getByRole("button", { name: "Cancel the booking" })).toBeTruthy();
  });

  it("cancels a booking made through an agent, after asking", async () => {
    open("?booking=R-014");
    const box = within(await dialog());
    fireEvent.click(box.getByRole("button", { name: "Cancel the booking" }));
    expect(box.getByText("Cancel this booking?").parentElement?.textContent).toBe(
      "Cancel this booking? Marie Lang sees a notice the next time their assistant checks.",
    );

    fireEvent.click(box.getByRole("button", { name: "Keep it" }));
    fireEvent.click(box.getByRole("button", { name: "Cancel the booking" }));
    fireEvent.click(box.getAllByRole("button", { name: "Cancel the booking" }).at(-1) as HTMLElement);

    expect(await toast("Marie Lang is cancelled")).toBe(
      "Marie Lang is cancelledTable T9 is free again. The guest sees a notice next time their assistant checks.",
    );
    await waitFor(() =>
      expect(screen.getByText("Marie Lang").closest(".who")?.textContent).toBe("Marie Lang4, T9Cancelled by you"),
    );
  });

  it("cancels the merchant's own booking without a notice", async () => {
    open("?booking=R-010");
    const box = within(await dialog());
    fireEvent.click(box.getByRole("button", { name: "Cancel the booking" }));
    expect(box.getByText("Cancel this booking?").parentElement?.textContent).toBe(
      "Cancel this booking? The table goes back on offer straight away.",
    );
    fireEvent.click(box.getAllByRole("button", { name: "Cancel the booking" }).at(-1) as HTMLElement);

    expect(await toast("Ines Schmitt is cancelled")).toBe("Ines Schmitt is cancelledTable T2 is free again.");
  });
});

describe("A link", () => {
  it("to a day past the next 7 opens today and says so", async () => {
    open("?date=2026-11-20&booking=R-099");
    expect(await screen.findByText("That booking is on Friday 20 November, past the next 7 days.")).toBeTruthy();
    expect(screen.getByText("Tonight, 6 bookings and 17 guests.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("A new booking", () => {
  it("offers the times to come, says which table, and adds it", async () => {
    const { client } = open();
    await screen.findByText("Tonight, 6 bookings and 17 guests.");
    click("New booking");
    const box = within(await dialog());
    expect(box.getByRole("heading", { name: "A new booking" })).toBeTruthy();
    const time = box.getByLabelText<HTMLSelectElement>("Time");
    await waitFor(() => expect([...time.options].map((o) => o.value)).toEqual(["20:00", "20:30"]));
    const note = () => box.getByRole("status").textContent;
    expect(note()).toBe("Table T4, 2 seats, until 22:00.");

    fireEvent.click(box.getByRole("button", { name: "Add the booking" }));
    expect(note()).toBe("Add the guest's name.");

    const party = box.getByLabelText("Guests");
    fireEvent.change(party, { target: { value: "0" } });
    expect(note()).toBe("A booking needs at least one guest.");
    fireEvent.change(party, { target: { value: "5" } });
    expect(note()).toBe("Your limit is 4. You can change it in the booking rules.");
    fireEvent.change(party, { target: { value: "4" } });
    await waitFor(() => expect(note()).toBe("No table for 4 is free on this day."));

    fireEvent.change(party, { target: { value: "2" } });
    fireEvent.change(box.getByLabelText("Guest's name"), { target: { value: "Ana Roth" } });
    await waitFor(() => expect(note()).toBe("Table T4, 2 seats, until 22:00."));
    fireEvent.click(box.getByRole("button", { name: "Add the booking" }));

    expect(await toast("Ana Roth is booked")).toBe("Ana Roth is booked20:00, 2 guests, table T4");
    const day = await client.reservations(SLUG, "2026-10-31");
    expect(day.reservations.find((b) => b.guest_name === "Ana Roth")).toMatchObject({ via: "merchant", time: "20:00" });
    await waitFor(() => expect(document.querySelector(".who.fresh")?.textContent).toBe("Ana Roth2, T4By you"));
  });

  it("opens from a link with its time and party", async () => {
    open("?date=2026-11-01&new=12:30&guests=1");
    const box = within(await dialog());
    expect(box.getByText("Sunday 1 November")).toBeTruthy();
    await waitFor(() => expect(box.getByLabelText<HTMLSelectElement>("Time").value).toBe("12:30"));
    expect(box.getByLabelText<HTMLInputElement>("Guests").value).toBe("1");
    expect(screen.getByRole("button", { name: "Lunch, 11:30–14:30" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("never offers a blocked time", async () => {
    open("?date=2026-11-01&new=");
    const box = within(await dialog());
    const time = box.getByLabelText<HTMLSelectElement>("Time");
    await waitFor(() => expect(time.options.length).toBeGreaterThan(0));
    expect([...time.options].map((o) => o.value)).not.toContain("13:00");
  });
});

describe("Block a time", () => {
  it("blocks a time to come and marks its column", async () => {
    open();
    await screen.findByText("Tonight, 6 bookings and 17 guests.");
    click("Block a time");
    const box = within(await dialog());
    // 19:40: only 20:00 and 20:30 are still to come tonight.
    expect(box.queryByLabelText("19:00")).toBeNull();
    fireEvent.click(box.getByLabelText("20:00"));
    fireEvent.click(box.getByRole("button", { name: "Save" }));

    expect(await toast("Blocked 20:00")).toBe("Blocked 20:00Agents stop offering it.");
    await waitFor(() => expect(document.querySelector(".col.blocked h3")?.textContent).toBe("3 bookings, 8 guests, blocked"));
  });
});

describe("A live booking", () => {
  it("appears in its column, highlighted", async () => {
    // After the page's first read, so that the booking is new to it, even on a slow runner.
    open("", 400);
    await screen.findByText("Tonight, 6 bookings and 17 guests.");
    await waitFor(() => expect(screen.getByText("Tonight, 7 bookings and 19 guests.")).toBeTruthy(), { timeout: 3000 });
    expect(document.querySelector(".who.fresh")?.textContent).toBe("Kim Weber2, T4Through your agent");
  });
});
