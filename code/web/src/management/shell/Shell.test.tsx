import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import fixtureJson from "../../../../tests/fixtures/demo/la-graine-verte-management.json" with { type: "json" };
import { createMockManagementClient } from "../../mocks/managementClient.ts";
import { routes } from "../../router.tsx";
import type { ManagementFixture } from "../../types.ts";
import type { ManagementClient } from "../client.ts";
import { ClientContext } from "../data.ts";
import { NOTHING_FOUND } from "./TopBar.tsx";

const fixture = fixtureJson as ManagementFixture;
const SLUG = "la-graine-verte";

// The demo fixture, answering at once; its agent books Kim Weber after `liveBookingDelayMs`.
function mock(liveBookingDelayMs = 60_000, changes: Partial<ManagementFixture> = {}) {
  return createMockManagementClient({ ...fixture, ...changes }, { latencyMs: 0, liveBookingDelayMs });
}

function at(path: string, client: ManagementClient = mock()) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <ClientContext.Provider value={client}>
      <RouterProvider router={router} />
    </ClientContext.Provider>,
  );
  return router;
}

const where = (router: ReturnType<typeof at>) => router.state.location.pathname + router.state.location.search;

describe("sidebar", () => {
  it("lists the pages in the mockup's order, Settings apart", () => {
    at(`/manage/${SLUG}`);
    const main = screen.getByRole("navigation", { name: "Main" });
    expect(within(main).getAllByRole("link").map((a) => a.textContent)).toEqual([
      "Home",
      "Assistant",
      "Reservations",
      "Guests",
      "Opening hours",
      "Floor plan",
      "Booking rules",
      "Agent",
    ]);
    // The heading sits between the evening's pages and the restaurant's.
    const order = [...main.children].map((el) => el.textContent);
    expect(order.indexOf("Restaurant")).toBe(4);
    const account = screen.getByRole("navigation", { name: "Account" });
    expect(within(account).getAllByRole("link").map((a) => a.textContent)).toEqual(["Settings"]);
  });

  it("marks the current page, and only it", () => {
    at(`/manage/${SLUG}/floor`);
    const current = screen.getAllByRole("link").filter((a) => a.getAttribute("aria-current") === "page");
    expect(current.map((a) => a.textContent)).toEqual(["Floor plan"]);
  });

  it("names the restaurant, its city and its price", async () => {
    at(`/manage/${SLUG}/rules`);
    expect(screen.getByRole("link", { name: "La Graine Verte" }).getAttribute("href")).toBe(`/manage/${SLUG}`);
    expect(await screen.findByText("Mulhouse, €")).toBeTruthy();
  });
});

describe("search", () => {
  const box = () => screen.getByRole("textbox", { name: "Search a guest, a booking or a page" });
  const results = async () => screen.findByRole("group", { name: "Search results" });

  it("finds pages by name, then bookings by guest", async () => {
    const user = userEvent.setup();
    at(`/manage/${SLUG}`);
    await user.type(box(), "sara");
    const hits = await results();
    const sara = await within(hits).findByRole("button", { name: /Sara Muller, 2/ });
    expect(sara.textContent).toBe("Sara Muller, 2Sat 31 Oct 18:30");
    expect(within(hits).getAllByRole("button").map((b) => b.textContent?.split(",")[0])).toEqual([
      "Sara Muller",
      "Sara Weiss",
      "Sara Frey",
    ]);

    await user.clear(box());
    await user.type(box(), "floor");
    expect((await screen.findByRole("button", { name: /Floor plan/ })).textContent).toBe("Floor planPage");
  });

  it("marks a cancelled booking", async () => {
    const user = userEvent.setup();
    at(`/manage/${SLUG}`);
    await user.type(box(), "max martin");
    const hit = await screen.findByRole("button", { name: /Max Martin/ });
    expect(hit.textContent).toBe("Max Martin, 2, cancelledSun 1 Nov 12:00");
    expect(hit.className).toBe("off");
  });

  it("says when nothing matches", async () => {
    const user = userEvent.setup();
    at(`/manage/${SLUG}`);
    await user.type(box(), "zzz");
    expect(await screen.findByText(NOTHING_FOUND)).toBeTruthy();
  });

  it("opens the first result on Enter and clears the input", async () => {
    const user = userEvent.setup();
    const router = at(`/manage/${SLUG}`);
    await user.type(box(), "sara");
    await within(await results()).findByRole("button", { name: /Sara Muller/ });
    await user.keyboard("{Enter}");
    expect(where(router)).toBe(`/manage/${SLUG}/reservations?date=2026-10-31&booking=R-009`);
    expect((box() as HTMLInputElement).value).toBe("");
    expect(screen.queryByRole("group", { name: "Search results" })).toBeNull();
  });

  it("goes to a page it found", async () => {
    const user = userEvent.setup();
    const router = at(`/manage/${SLUG}`);
    await user.type(box(), "rules");
    await user.click(await screen.findByRole("button", { name: /Booking rules/ }));
    expect(where(router)).toBe(`/manage/${SLUG}/rules`);
    expect((box() as HTMLInputElement).value).toBe("");
  });

  it("closes on Esc and on a click outside", async () => {
    const user = userEvent.setup();
    at(`/manage/${SLUG}`);
    await user.type(box(), "floor");
    await screen.findByRole("button", { name: /Floor plan.*Page/ });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("button", { name: /Floor plan.*Page/ })).toBeNull();

    await user.clear(box());
    await user.type(box(), "s");
    await screen.findByRole("button", { name: /Settings.*Page/ });
    await user.click(screen.getByRole("heading", { level: 1 }));
    expect(screen.queryByRole("button", { name: /Settings.*Page/ })).toBeNull();
  });

  it("takes the focus on Cmd+K or Ctrl+K", async () => {
    at(`/manage/${SLUG}`);
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    expect(document.activeElement).toBe(box());
    (document.activeElement as HTMLElement).blur();
    fireEvent.keyDown(document, { key: "K", metaKey: true });
    expect(document.activeElement).toBe(box());
  });
});

describe("top bar", () => {
  it("shows the platform's clock in the merchant's zone and the owner", async () => {
    at(`/manage/${SLUG}`);
    expect((await screen.findByText(/^Sat 31 Oct, 19:4\d$/)).className).toBe("clock");
    expect(screen.getByRole("img", { name: "Camille" }).textContent).toBe("C");
  });

  it("names the restaurant on the avatar until the owner gave a name", async () => {
    at(`/manage/${SLUG}`, mock(60_000, { merchant: { ...fixture.merchant, owner_name: null } }));
    expect((await screen.findByRole("img", { name: "La Graine Verte" })).textContent).toBe("LG");
  });
});

describe("live bookings", () => {
  const bell = () => screen.getByRole("button", { name: "Notifications" });

  it("count on the bell until its popover is opened", async () => {
    const user = userEvent.setup();
    at(`/manage/${SLUG}/rules`, mock(10));
    expect(bell().querySelector("i")).toBeNull();
    await waitFor(() => expect(bell().querySelector("i")?.textContent).toBe("1"));

    await user.click(bell());
    expect(bell().querySelector("i")).toBeNull();
    const pop = screen.getByRole("heading", { name: "Notifications" }).parentElement!;
    await waitFor(() => expect(pop.querySelectorAll("p")).toHaveLength(5));
    expect(pop.querySelector("p")?.textContent).toMatch(/^19:4\d, new booking: R-056, Kim Weber, 2 guests, today 20:00$/);

    await user.click(screen.getByRole("heading", { level: 1 }));
    expect(screen.queryByRole("heading", { name: "Notifications" })).toBeNull();
  });

  it("show a toast while the assistant is closed, which opens the booking", async () => {
    const user = userEvent.setup();
    const router = at(`/manage/${SLUG}/rules`, mock(10));
    const toast = await screen.findByText("New booking through your agent");
    expect(toast.parentElement?.textContent).toBe("New booking through your agentKim Weber, 20:00, 2 guests, T4");
    await user.click(screen.getByRole("button", { name: "See it" }));
    expect(where(router)).toBe(`/manage/${SLUG}/reservations?date=2026-10-31&booking=R-056`);
  });

  it("show no toast while the assistant is open, but still ring", async () => {
    const user = userEvent.setup();
    at(`/manage/${SLUG}`, mock(200));
    // Hidden, the column has no accessible name to find it by.
    const panel = document.querySelector<HTMLElement>("aside.asst")!;
    expect(panel.hidden).toBe(true);
    await user.click(screen.getByRole("button", { name: "Ask the assistant" }));
    expect(panel.hidden).toBe(false);
    await waitFor(() => expect(bell().querySelector("i")?.textContent).toBe("1"));
    expect(screen.queryByText("New booking through your agent")).toBeNull();
  });

  it("appear on Home without a reload", async () => {
    // Home reads tonight first, then the live booking arrives: on a slow runner, 10 ms let it
    // land in the first read, where nothing is new.
    at(`/manage/${SLUG}`, mock(400));
    await screen.findByRole("button", { name: /Sara Muller/ });
    const row = await screen.findByRole("button", { name: /Kim Weber/ }, { timeout: 3000 });
    expect(row.className).toContain("fresh");
  });
});

describe("the site's own changes", () => {
  it("refresh the shell: a new name shows in the sidebar", async () => {
    const user = userEvent.setup();
    at(`/manage/${SLUG}/settings`);
    const name = await screen.findByLabelText("Name");
    await user.clear(name);
    await user.type(name, "La Graine");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("link", { name: "La Graine" })).toBeTruthy();
    await waitFor(() => expect(document.title).toBe("La Graine · Settings"));
  });
});

describe("assistant column", () => {
  it("closes, and opens on its own page from the expand button", async () => {
    const user = userEvent.setup();
    const router = at(`/manage/${SLUG}`);
    await user.click(screen.getByRole("button", { name: "Ask the assistant" }));
    const panel = screen.getByRole("complementary", { name: "Assistant" });
    await user.click(within(panel).getByRole("button", { name: "Close the assistant" }));
    expect(panel.hidden).toBe(true);

    await user.click(within(panel).getByRole("button", { name: "Open the assistant on a full page", hidden: true }));
    expect(where(router)).toBe(`/manage/${SLUG}/assistant`);
    await user.click(screen.getByRole("button", { name: "Back to the dashboard" }));
    expect(where(router)).toBe(`/manage/${SLUG}`);
  });
});

describe("footer", () => {
  it("says the demo's data is made up, only for a demo merchant", async () => {
    at(`/manage/${SLUG}/rules`);
    expect(await screen.findByText("Demo data. La Graine Verte is a fictional restaurant.")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Sésame" })).toBeTruthy();
  });

  it("has only the wordmark for a real merchant", async () => {
    at(`/manage/${SLUG}/rules`, mock(60_000, { merchant: { ...fixture.merchant, demo: false } }));
    await screen.findByText("Mulhouse, €");
    expect(screen.queryByText(/Demo data/)).toBeNull();
    // The wordmark reads "Sesame", its seed drawn on the e; its name is "Sésame".
    expect(screen.getByRole("contentinfo").textContent).toBe("Run on Sesame");
    expect(within(screen.getByRole("contentinfo")).getByRole("img", { name: "Sésame" })).toBeTruthy();
  });

  it("isn't on the Assistant page", () => {
    at(`/manage/${SLUG}/assistant`);
    expect(screen.queryByRole("contentinfo")).toBeNull();
  });
});
