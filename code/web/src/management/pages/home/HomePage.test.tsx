import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import fixtureJson from "../../../../../tests/fixtures/demo/la-graine-verte-management.json" with { type: "json" };
import { DEMO_NOW, setClock } from "../../../clock.ts";
import { createMockManagementClient } from "../../../mocks/managementClient.ts";
import type { Dashboard, ManagementFixture, MerchantOverview, ReservationDay } from "../../../types.ts";
import type { ManagementClient } from "../../client.ts";
import { ClientContext } from "../../data.ts";
import { HomePage, type HomeProps, NOTHING_TONIGHT, NO_ACTIVITY } from "./HomePage.tsx";
import { NO_HISTORY } from "./GuestsCard.tsx";

const fixture = fixtureJson as ManagementFixture;
const SLUG = "la-graine-verte";

function mock(liveBookingDelayMs = 60_000) {
  return createMockManagementClient(fixture, { latencyMs: 0, liveBookingDelayMs });
}

function show({ client, ...props }: Partial<HomeProps> & { client: ManagementClient }) {
  const tree = (next: Partial<HomeProps> = {}) => (
    <ClientContext.Provider value={client}>
      <MemoryRouter>
        <div className="mg">
          <HomePage slug={SLUG} {...props} {...next} />
        </div>
      </MemoryRouter>
    </ClientContext.Provider>
  );
  const view = render(tree());
  return { ...view, again: (next: Partial<HomeProps>) => view.rerender(tree(next)) };
}

const card = (name: string) => screen.getByRole("region", { name });

describe("Home with the demo fixture", () => {
  it("greets the owner by the evening clock and names tonight's service", async () => {
    setClock(DEMO_NOW);
    show({ client: mock() });
    expect(await screen.findByText("Saturday 31 October, dinner 18:30–21:30")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Good evening, Camille");
  });

  it("says good morning before noon", async () => {
    setClock(new Date("2026-10-31T09:10:00+01:00"));
    show({ client: mock() });
    await screen.findByText(/Saturday 31 October/);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Good morning, Camille");
  });

  it("fills every card", async () => {
    setClock(DEMO_NOW);
    show({ client: mock() });
    await screen.findByRole("region", { name: "Guests" });

    const guests = card("Guests");
    expect(guests.textContent).toContain("982guests in the last 30 days");
    expect(guests.textContent).toContain("597 through your agent");
    expect(guests.textContent).toContain("42 cancelled");
    expect(within(guests).getByRole("img", { name: "Guests per day over the last 30 days" })).toBeTruthy();

    const tonight = card("Tonight");
    expect(tonight.textContent).toContain("17 guests, 6 bookings");
    const rows = within(tonight).getAllByRole("button");
    expect(rows).toHaveLength(6);
    expect(rows[0].textContent).toBe("18:30Sara Muller2, T1Agent");
    // Live bookings only, like the header.
    expect(within(tonight).getByRole("link").textContent).toBe("See all 6 bookings");

    const week = card("Next 7 days");
    expect(week.textContent).toContain("146guests booked");
    const bars = within(week).getAllByRole("button");
    expect(bars).toHaveLength(7);
    expect(bars[0].getAttribute("aria-label")).toBe("Today, Sat 31 Oct, 39 guests");
    expect(bars[0].className).toBe("bar today");

    const sources = card("Where bookings come from");
    expect(sources.textContent).toContain("61%through your agent");
    expect(sources.textContent).toContain("Agent 61%");
    expect(sources.textContent).toContain("You 39%");
    expect(sources.textContent).toContain("375 bookings in the last 30 days");

    const room = card("The room tonight");
    expect(room.textContent).toContain("17of 24 seats booked");
    const minis = room.querySelectorAll(".mini");
    expect(minis).toHaveLength(9);
    expect([...minis].filter((m) => m.classList.contains("on")).map((m) => m.textContent)).toEqual([
      "T1",
      "T2",
      "T3",
      "T7",
      "T8",
      "T9",
    ]);
    expect(room.textContent).toContain("Groups up to 4");

    const activity = card("Recent activity");
    expect(activity.querySelectorAll(".act").length).toBeLessThanOrEqual(6);
    expect(activity.textContent).toContain("R-009, Sara Muller, 2 guests, today 18:30");
  });

  it("keeps a booking cancelled tonight in the list, greyed, and out of the count", async () => {
    setClock(DEMO_NOW);
    const client = mock();
    await client.cancelBooking(SLUG, "R-014");
    show({ client });
    await screen.findByText("13 guests, 5 bookings");

    const tonight = card("Tonight");
    const rows = within(tonight).getAllByRole("button");
    expect(rows).toHaveLength(6);
    expect(rows.find((row) => row.textContent?.includes("Marie Lang"))?.className).toBe("t-row off");
    expect(within(tonight).getByRole("link").textContent).toBe("See all 5 bookings");
  });

  it("switches the guests chart to a table, newest day first", async () => {
    setClock(DEMO_NOW);
    show({ client: mock() });
    await userEvent.click(await screen.findByRole("button", { name: "Table" }));
    const rows = within(card("Guests")).getAllByRole("row");
    expect(rows).toHaveLength(31);
    expect(rows[1].textContent).toBe("Sat 31 Oct39240");
    expect(screen.getByRole("button", { name: "Table" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("hands its actions to the shell", async () => {
    setClock(DEMO_NOW);
    const onAsk = vi.fn();
    const onBlock = vi.fn();
    const onOpenBooking = vi.fn();
    const onOpenDay = vi.fn();
    show({ client: mock(), onAsk, onBlock, onOpenBooking, onOpenDay });
    await screen.findByRole("region", { name: "Tonight" });
    await userEvent.click(screen.getByRole("button", { name: "Ask the assistant" }));
    await userEvent.click(screen.getByRole("button", { name: "Block a time" }));
    await userEvent.click(within(card("Tonight")).getAllByRole("button")[0]);
    await userEvent.click(screen.getByRole("button", { name: "Sun 1 Nov, 20 guests" }));
    expect(onAsk).toHaveBeenCalledOnce();
    expect(onBlock).toHaveBeenCalledWith("2026-10-31");
    expect(onOpenBooking).toHaveBeenCalledWith("R-009", "2026-10-31");
    expect(onOpenDay).toHaveBeenCalledWith("2026-11-01");
  });

  it("moves the numbers and highlights the new rows after the live booking", async () => {
    setClock(DEMO_NOW);
    const client = mock(0);
    const view = show({ client, version: 0 });
    await screen.findByText("17 guests, 6 bookings");
    expect(document.querySelectorAll(".fresh")).toHaveLength(0);

    await new Promise<void>((resolve) => {
      const stop = client.subscribe(SLUG, {
        onEvent: () => {
          stop();
          resolve();
        },
        onConnection: () => {},
      });
    });
    view.again({ version: 1 });

    expect(await screen.findByText("19 guests, 7 bookings")).toBeTruthy();
    expect(card("The room tonight").textContent).toContain("19of 24 seats booked");
    expect(card("Next 7 days").textContent).toContain("148guests booked");
    expect(card("Guests").textContent).toContain("984guests in the last 30 days");
    const fresh = [...document.querySelectorAll(".fresh")];
    expect(fresh.map((el) => el.className)).toEqual(["t-row fresh", "act fresh"]);
    expect(fresh[0].textContent).toContain("Kim Weber");
  });
});

describe("Home for a merchant with no bookings", () => {
  const merchant = { ...fixture.merchant, owner_name: null } satisfies MerchantOverview;
  const days = (n: number, from: string) =>
    Array.from({ length: n }, (_, i) => {
      const d = new Date(`${from}T12:00:00Z`);
      d.setUTCDate(d.getUTCDate() + i);
      return d.toISOString().slice(0, 10);
    });
  const dashboard: Dashboard = {
    date: "2026-10-31",
    history: days(30, "2026-10-02").map((date) => ({ date, bookings: 0, guests: 0, agent_guests: 0, cancelled: 0 })),
    next_days: days(7, "2026-10-31").map((date) => ({ date, guests: 0 })),
    channels: [],
    room: {
      service: { start: "18:30", end: "21:30" },
      seats: 8,
      guests: 0,
      tables: [
        { label: "T1", seats: 4, booked: false },
        { label: "T2", seats: 4, booked: false },
      ],
      party_size: 4,
      next_free: "19:30",
      largest_group: 4,
    },
    activity: [],
  };
  const day: ReservationDay = {
    date: "2026-10-31",
    services: [{ start: "18:30", end: "21:30" }],
    starts: [],
    slot_length_min: 120,
    largest_group: 4,
    blocked: [],
    reservations: [],
  };
  const empty = {
    ...mock(),
    merchant: async () => merchant,
    dashboard: async () => dashboard,
    reservations: async () => day,
  } satisfies ManagementClient;

  it("shows each card's empty state", async () => {
    setClock(DEMO_NOW);
    show({ client: empty });
    expect(await screen.findByText(NO_HISTORY)).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Good evening");
    expect(within(card("Guests")).queryByRole("img")).toBeNull();
    expect(within(card("Guests")).queryByRole("button", { name: "Table" })).toBeNull();
    expect(within(card("Tonight")).getByText(NOTHING_TONIGHT)).toBeTruthy();
    expect(within(card("Tonight")).queryByRole("link")).toBeNull();
    expect(card("Next 7 days").textContent).toContain("0guests booked");
    expect(card("Where bookings come from").textContent).toContain("0%through your agent");
    expect(card("Where bookings come from").textContent).toContain("0 bookings in the last 30 days");
    expect(card("The room tonight").textContent).toContain("Next table for 4, 19:30");
    expect(within(card("Recent activity")).getByText(NO_ACTIVITY)).toBeTruthy();
  });

  it("says so when the platform can't answer", async () => {
    const down = {
      ...empty,
      dashboard: async () => {
        throw new Error("I can't reach the platform right now.");
      },
    } satisfies ManagementClient;
    show({ client: down });
    expect((await screen.findByRole("alert")).textContent).toBe("I can't reach the platform right now.");
  });
});
