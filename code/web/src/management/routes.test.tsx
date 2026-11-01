import { cleanup, render, screen } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { DEMO_NOW, now, setClock } from "../clock.ts";
import { MOMENT_READY } from "../moments.ts";
import { ROUTE_ERROR, ROUTE_ERROR_TITLE } from "../RouteError.tsx";
import { ManagePage } from "./ManagePage.tsx";
import { routes as appRoutes, routesWith } from "../router.tsx";
import type { Dashboard } from "../types.ts";
import type { ManagementClient } from "./client.ts";
import { ClientContext } from "./data.ts";
import { managementClient } from "./index.ts";
import { PAGES, type PageId, nameFromSlug } from "./pages.ts";

// The management site drawn at once: the app loads it on its first visit (router.tsx).
const routes = routesWith(<ManagePage />);

// jsdom has no fetch target nor EventSource: the pages read the demo fixture.
vi.mock("./index.ts", async () => {
  const { createMockManagementClient } = await import("../mocks/managementClient.ts");
  const fixture = await import("../../../tests/fixtures/demo/la-graine-verte-management.json");
  return {
    managementClient: createMockManagementClient(fixture.default as never, {
      latencyMs: 0,
      liveBookingDelayMs: 60_000,
    }),
  };
});

function at(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(<RouterProvider router={router} />);
  return router;
}

describe("management routes", () => {
  const others = (Object.keys(PAGES) as PageId[]).filter((page) => page !== "home" && page !== "assistant");
  it.each(others)("/manage/:slug/%s has its title", (page) => {
    at(`/manage/la-graine-verte/${page}`);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(PAGES[page]);
    expect(document.title).toBe(`La Graine Verte · ${PAGES[page]}`);
  });

  it("/manage/:slug/assistant is the assistant on the whole page", () => {
    at("/manage/la-graine-verte/assistant");
    const panel = screen.getByRole("complementary", { name: "Assistant" });
    expect(panel.hidden).toBe(false);
    expect(screen.getByRole("heading", { level: 2, name: "Assistant" })).toBeTruthy();
    expect(screen.queryByRole("main")).toBeNull();
    expect(document.title).toBe("La Graine Verte · Assistant");
  });

  it("/manage/:slug is Home, which greets the owner", async () => {
    at("/manage/la-graine-verte");
    expect(document.title).toBe("La Graine Verte · Home");
    expect(await screen.findByRole("heading", { level: 1, name: /^Good (morning|afternoon|evening), Camille$/ })).toBeTruthy();
  });

  it("sends an unknown page to Home", () => {
    const router = at("/manage/la-graine-verte/nope");
    expect(router.state.location.pathname).toBe("/manage/la-graine-verte");
    const deeper = at("/manage/la-graine-verte/floor/extra");
    expect(deeper.state.location.pathname).toBe("/manage/la-graine-verte");
  });

  it("sends an unknown path to Studio", () => {
    const router = at("/nowhere");
    expect(router.state.location.pathname).toBe("/");
  });

  it("shows its own page, not React Router's developer screen, when a page fails to draw (#220)", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    function Broken(): never {
      throw new Error("a page that can't draw");
    }
    // The app's error page, with a page that throws in place of theirs.
    const router = createMemoryRouter([
      { errorElement: routes[0].errorElement, children: [{ path: "/", element: <Broken /> }] },
    ]);
    render(<RouterProvider router={router} />);

    expect((await screen.findByRole("alert")).textContent).toContain(ROUTE_ERROR);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(ROUTE_ERROR);
    expect(screen.getByRole("link", { name: "Back to Studio" }).getAttribute("href")).toBe("/");
    expect(screen.getByRole("button", { name: "Reload the page" })).toBeTruthy();
    expect(screen.queryByText(/Unexpected Application Error/)).toBeNull();
    expect(document.title).toBe(ROUTE_ERROR_TITLE);
    expect(screen.getByRole("main").textContent).toContain(ROUTE_ERROR);
    expect(screen.getByRole("banner")).toBeTruthy();
    // React and React Router say why in the console: the page doesn't log it again.
    expect(logged.mock.calls.flat()).toContainEqual(expect.objectContaining({ message: "a page that can't draw" }));
    logged.mockRestore();
  });

  it("catches a management page that fails to draw, through the app's own routes (#220)", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => {});
    // A dashboard without its room: Home reads it while it draws.
    const broken: ManagementClient = {
      ...managementClient,
      dashboard: async (slug, signal) => ({
        ...(await managementClient.dashboard(slug, signal)),
        room: undefined as unknown as Dashboard["room"],
      }),
    };
    render(
      <ClientContext.Provider value={broken}>
        <RouterProvider router={createMemoryRouter(appRoutes, { initialEntries: ["/manage/la-graine-verte"] })} />
      </ClientContext.Provider>,
    );

    expect((await screen.findByRole("alert")).textContent).toContain(ROUTE_ERROR);
    // The whole shell gives way, not just Home.
    expect(screen.queryByRole("navigation", { name: "Main" })).toBeNull();
    expect(screen.queryByText(/Unexpected Application Error/)).toBeNull();
    expect(screen.getByRole("link", { name: "Back to Studio" })).toBeTruthy();
    logged.mockRestore();
  });

  it("loads the management site on its first visit, through the app's own routes (#220)", async () => {
    render(<RouterProvider router={createMemoryRouter(appRoutes, { initialEntries: ["/manage/la-graine-verte/hours"] })} />);
    expect(await screen.findByRole("heading", { level: 1, name: PAGES.hours })).toBeTruthy();
  });

  it("names the merchant from its slug until the API gives the name", () => {
    expect(nameFromSlug("le-petit-zinc")).toBe("Le Petit Zinc");
  });
});

// Studio sends the moment it shows once the page in its frame listens (#220).
describe("management site in Studio's frame", () => {
  it("tells its parent it listens for moments, only when framed", async () => {
    const parent = { postMessage: vi.fn() };
    const framed = vi.spyOn(window, "parent", "get").mockReturnValue(parent as unknown as Window);
    at("/manage/la-graine-verte");
    await vi.waitFor(() => expect(parent.postMessage).toHaveBeenCalledWith(MOMENT_READY, window.location.origin));
    framed.mockRestore();
    cleanup();

    const own = vi.spyOn(window, "postMessage");
    at("/manage/la-graine-verte");
    expect(await screen.findByRole("heading", { level: 1 })).toBeTruthy();
    expect(own).not.toHaveBeenCalled();
    own.mockRestore();
  });
});

describe("clock", () => {
  it("can be fixed at the demo's Saturday 19:40", () => {
    setClock(DEMO_NOW);
    expect(now().toISOString()).toBe("2026-10-31T18:40:00.000Z");
  });
});
