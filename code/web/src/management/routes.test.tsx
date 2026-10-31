import { render, screen } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { DEMO_NOW, now, setClock } from "../clock.ts";
import { routes } from "../router.tsx";
import { PAGES, type PageId, nameFromSlug } from "./pages.ts";

function at(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(<RouterProvider router={router} />);
  return router;
}

describe("management routes", () => {
  it.each(Object.keys(PAGES) as PageId[])("/manage/:slug/%s has its title", (page) => {
    at(page === "home" ? "/manage/la-graine-verte" : `/manage/la-graine-verte/${page}`);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(PAGES[page]);
    expect(document.title).toBe(`La Graine Verte · ${PAGES[page]}`);
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

  it("names the merchant from its slug until the API gives the name", () => {
    expect(nameFromSlug("le-petit-zinc")).toBe("Le Petit Zinc");
  });
});

describe("clock", () => {
  it("can be fixed at the demo's Saturday 19:40", () => {
    setClock(DEMO_NOW);
    expect(now().toISOString()).toBe("2026-10-31T18:40:00.000Z");
  });
});
