import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { AgentTryOutput, BuildFact, DaySlots, Look, SitePage } from "../types.ts";
import { Canvas } from "./Canvas.tsx";

const HOME: SitePage = { path: "/", width: 1000, height: 2000, image: "data:image/jpeg;base64,AA==" };
const HOURS: SitePage = { ...HOME, path: "/horaires.html" };
const FACT: BuildFact = {
  key: "max_party_size",
  label: "Largest group",
  value: "4",
  source: "/horaires.html",
  quote: "Tables de 1 à 4",
  box: { page: "/horaires.html", x: 100, y: 200, width: 300, height: 40 },
};

const LOOK: Look = {
  bg: "#edebe2",
  surface: "#f8f6ef",
  line: "#d5d2c3",
  ink: "#47522f",
  muted: "#5f6a45",
  accent: "#727f55",
  accent_2: "#935046",
  on_accent: "#f4f2ea",
  display_font: "Fraunces Soft",
  body_font: "Karla",
  corners: "organic",
  radius_px: 30,
  texture: "leather",
};

class FakeObserver {
  observe() {}
  disconnect() {}
  unobserve() {}
}

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", FakeObserver);
  // jsdom has no layout: 500 wide is half a frame, so 50 %.
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, value: 500 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, value: 700 });
});

afterEach(cleanup);

function client(frames?: AgentTryOutput) {
  const day: DaySlots = {
    date: "2026-10-31",
    party_size: 2,
    slots: [{ starts_at: "2026-10-31T20:30:00+01:00", time: "20:30", until: "22:30", table: { label: "T8", seats: 4 }, blocked: false }],
  };
  return {
    agentRequests: vi.fn(async () => ({ requests: [] })),
    slots: vi.fn(async () => day),
    tryAgent: vi.fn(async () => frames ?? { request: "{}", response: "{}", status: 200 }),
  };
}

function canvas(props: Partial<Parameters<typeof Canvas>[0]> = {}) {
  return render(
    <Canvas
      client={client()}
      slug="la-graine-verte"
      live={false}
      today="2026-10-31"
      ready
      view="manage"
      addresses={null}
      pages={[HOME]}
      fact={null}
      tools={[]}
      fallback={null}
      onView={() => {}}
      moment={null}
      services={[{ start: "11:30", end: "14:30" }, { start: "18:30", end: "21:30" }]}
      look={LOOK}
      onMoment={() => {}}
      onColour={() => {}}
      {...props}
    />,
  );
}

const tab = (name: string | RegExp) => screen.getByRole("button", { name });

describe("Studio's canvas", () => {
  it("has its tabs in order, disabled until built, except the public one", () => {
    canvas({ slug: null, view: "public" });
    const group = screen.getByRole("group", { name: "Which site to show" });
    expect([...group.querySelectorAll("button")].map((button) => button.textContent)).toEqual([
      "AgAgent site",
      "MgManagement",
      "Both",
      "WsYour public site",
    ]);
    expect(tab(/Agent site/).hasAttribute("disabled")).toBe(true);
    expect(tab(/Management/).hasAttribute("disabled")).toBe(true);
    expect(tab("Both").hasAttribute("disabled")).toBe(true);
    expect(tab(/Your public site/).hasAttribute("disabled")).toBe(false);
  });

  it("keeps the public tab disabled until a page was read, and opens the others once built", () => {
    canvas({ pages: [] });
    expect(tab(/Your public site/).hasAttribute("disabled")).toBe(true);
    expect(tab("Both").hasAttribute("disabled")).toBe(false);
  });

  it("shows the zoom it scaled the frames to", () => {
    canvas();
    expect(screen.getByText("50%")).toBeTruthy();
  });

  it("outlines a fact on the page it came from, with its label", () => {
    const { container } = canvas({ view: "public", pages: [HOME, HOURS], fact: FACT });
    const box = container.querySelector<HTMLElement>(".st-sel");
    expect(box?.dataset.tag).toBe("Largest group");
    expect([box?.style.left, box?.style.top, box?.style.width, box?.style.height]).toEqual(["100px", "200px", "300px", "40px"]);
    expect(screen.getByRole("img").getAttribute("alt")).toBe("/horaires.html as read");
    expect(screen.getByText("Images and web fonts aren't loaded when your site is read, so photos don't show here.")).toBeTruthy();
  });

  it("draws no outline for a fact with no box", () => {
    const { container } = canvas({ view: "public", fact: { ...FACT, box: null } });
    expect(container.querySelector(".st-sel")).toBeNull();
  });

  it("keeps the management frame mounted behind another tab", () => {
    const { container } = canvas({ view: "agent" });
    expect(container.querySelector("iframe")?.getAttribute("src")).toBe("/manage/la-graine-verte");
    expect(container.querySelector("iframe")?.closest(".st-frame")?.hasAttribute("hidden")).toBe(true);
  });

  it("waits for the owner cookie before loading the management frame", () => {
    const { container } = canvas({ ready: false });
    expect(container.querySelector("iframe")).toBeNull();
  });

  it("shows, unpublished, what an agent will get, and says it gets a 404 for now", async () => {
    const { container } = canvas({ view: "agent", addresses: { agent: "batp://h/la-graine-verte", mcp: "https://h/m/x/mcp", management: "https://h/manage/x" } });
    expect(screen.getByText(/Right now every agent gets a 404/)).toBeTruthy();
    expect(screen.getByText("Not live")).toBeTruthy();
    await waitFor(() => expect(container.querySelector(".st-w-log")?.textContent).toContain('"intent":"AVAILABILITY"'));
    expect(container.querySelector(".st-w-log")?.textContent).toContain('"table_label":"T8"');
  });

  it("sends a real frame from its button, and shows both frames with the status coloured", async () => {
    const frames = {
      request: '{"type":"request","id":"r-1","intent":"BOOK","merchant":"la-graine-verte"}',
      response: '{"type":"response","id":"r-1","status":404,"error":{"code":"not_found"}}',
      status: 404,
    };
    const fake = client(frames);
    const { container } = canvas({ view: "agent", client: fake });
    await userEvent.click(screen.getByRole("button", { name: "BOOK" }));
    expect(fake.tryAgent).toHaveBeenCalledWith("la-graine-verte", "BOOK");
    await waitFor(() => expect(container.querySelector(".st-w-log")?.textContent).toContain('"intent":"BOOK"'));
    const statuses = [...container.querySelectorAll(".st-w-s.bad")].map((item) => item.textContent);
    expect(statuses).toEqual(["404"]);
  });

  it("shows the dock on the management site and on both, with each moment's time", () => {
    const { container, unmount } = canvas({ view: "manage" });
    const moments = within(screen.getByRole("group", { name: "Time of day" })).getAllByRole("button");
    expect(moments.map((button) => [button.getAttribute("title"), button.textContent])).toEqual([
      ["Morning", "10:00"],
      ["Before service", "17:30"],
      ["During service", "19:30"],
      ["After service", "22:30"],
    ]);
    expect(container.querySelector(".st-dock-font")?.textContent).toBe("Fraunces");
    expect(container.querySelectorAll(".st-dots i")).toHaveLength(4);
    expect(screen.getAllByRole("button", { name: "Change the colours" })).toHaveLength(1);
    unmount();
    canvas({ view: "both" });
    expect(screen.getByRole("group", { name: "Time of day" })).toBeTruthy();
  });

  it("has no dock on the agent site or the public site, nor before the build", () => {
    const first = canvas({ view: "agent" });
    expect(screen.queryByRole("group", { name: "Time of day" })).toBeNull();
    first.unmount();
    const second = canvas({ view: "public" });
    expect(screen.queryByRole("group", { name: "Time of day" })).toBeNull();
    second.unmount();
    canvas({ view: "manage", slug: null });
    expect(screen.queryByRole("group", { name: "Time of day" })).toBeNull();
  });

  it("asks for a moment, and for a colour in words", async () => {
    const onMoment = vi.fn();
    const onColour = vi.fn();
    canvas({ view: "manage", moment: "during", onMoment, onColour });
    const during = screen.getByRole("button", { name: "19:30, During service" });
    expect(during.getAttribute("aria-pressed")).toBe("true");
    await userEvent.click(screen.getByRole("button", { name: "22:30, After service" }));
    expect(onMoment).toHaveBeenCalledWith("after");
    await userEvent.click(screen.getByRole("button", { name: "Change the colours" }));
    expect(onColour).toHaveBeenCalledOnce();
  });

  it("says why a frame couldn't be sent", async () => {
    const fake = client();
    fake.tryAgent.mockRejectedValueOnce(new Error("Sending frames needs the BATP node."));
    canvas({ view: "agent", client: fake });
    await userEvent.click(screen.getByRole("button", { name: "PING" }));
    expect((await screen.findByRole("alert")).textContent).toContain("needs the BATP node");
  });
});
