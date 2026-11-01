import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { MOMENT_READY } from "../moments.ts";
import type { AgentTryOutput, BuildFact, DaySlots, Look, Moment, SitePage } from "../types.ts";
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
  accent: "#616c48",
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
    talkToAgent: vi.fn(async (): Promise<AgentTryOutput> => ({ request: "{}", response: "{}", status: 200 })),
  };
}

function CanvasWith(props: Partial<Parameters<typeof Canvas>[0]>) {
  return (
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
    />
  );
}

const canvas = (props: Partial<Parameters<typeof Canvas>[0]> = {}) => render(<CanvasWith {...props} />);

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

  // "Ag Agent site" read "AgAgent site" (#220): the letters are the tile's picture.
  it("names its tabs without their tiles' letters", () => {
    canvas();
    const group = within(screen.getByRole("group", { name: "Which site to show" }));
    for (const name of ["Agent site", "Management", "Both", "Your public site"]) {
      expect(group.getByRole("button", { name })).toBeTruthy();
    }
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
    expect(screen.getByText(/Until you publish, your agent answers every message with a 404/)).toBeTruthy();
    expect(screen.getByText("Not live")).toBeTruthy();
    await waitFor(() => expect(container.querySelector(".st-w-log")?.textContent).toContain('"intent":"TALK"'));
    expect(container.querySelector(".st-w-log")?.textContent).toContain('"table_label":"T8"');
  });

  it("sends a real frame from its button, and shows both frames with the status coloured", async () => {
    const frames = {
      request: '{"type":"request","id":"r-1","intent":"RECALL","params":{"guest_name":"Test guest"}}',
      response: '{"type":"response","id":"r-1","status":503,"error":{"code":"agent_unavailable"}}',
      status: 503,
    };
    const fake = client(frames);
    const { container } = canvas({ view: "agent", client: fake });
    await userEvent.click(screen.getByRole("button", { name: "RECALL" }));
    expect(fake.tryAgent).toHaveBeenCalledWith("la-graine-verte", "RECALL");
    await waitFor(() => expect(container.querySelector(".st-w-log")?.textContent).toContain('"intent":"RECALL"'));
    const statuses = [...container.querySelectorAll(".st-w-s.bad")].map((item) => item.textContent);
    expect(statuses).toEqual(["503"]);
  });

  describe("asking the merchant's agent", () => {
    const answer = {
      conversation: "c-1",
      reply: "Yes, two vegetarian mains tonight. Shall I book 20:00?",
      slots: [],
      pending: {
        id: "p-1",
        action: "book" as const,
        summary: "Book a table for 2 tonight at 20:00.",
        expires_at: "2026-10-31T20:00:00+01:00",
      },
      reservation: null,
      fallback_url: null,
      tools_called: ["get_availability", "propose_booking"],
    };
    const proposed = {
      request: '{"type":"request","id":"r-1","intent":"TALK"}',
      response: '{"type":"response","id":"r-1","status":200}',
      status: 200,
      talk: answer,
    };

    it("sends the message and shows the reply, the tools and both frames", async () => {
      const fake = client();
      fake.talkToAgent.mockResolvedValueOnce(proposed);
      const { container } = canvas({ view: "agent", client: fake });
      await userEvent.type(screen.getByLabelText("Ask your agent"), "Vegetarian dishes?");
      await userEvent.click(screen.getByRole("button", { name: "Ask" }));
      expect(fake.talkToAgent).toHaveBeenCalledWith("la-graine-verte", {
        message: "Vegetarian dishes?",
        conversation: null,
        confirm: null,
      });
      expect(await screen.findByText(answer.reply)).toBeTruthy();
      expect([...container.querySelectorAll(".st-w-reply code")].map((c) => c.textContent)).toEqual(
        answer.tools_called,
      );
      expect(container.querySelector(".st-w-log")?.textContent).toContain('"intent":"TALK"');
      expect([...container.querySelectorAll(".st-w-s.ok")].length).toBeGreaterThan(0);
      expect((screen.getByLabelText("Ask your agent") as HTMLInputElement).value).toBe("");
    });

    it("offers Confirm while a proposal waits, and a confirm carries its conversation", async () => {
      const fake = client();
      fake.talkToAgent.mockResolvedValueOnce(proposed);
      canvas({ view: "agent", client: fake });
      expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
      await userEvent.type(screen.getByLabelText("Ask your agent"), "A table for 2?");
      await userEvent.click(screen.getByRole("button", { name: "Ask" }));
      await screen.findByText("Book a table for 2 tonight at 20:00.");
      fake.talkToAgent.mockResolvedValueOnce({
        ...proposed,
        talk: { ...answer, reply: "It's booked.", pending: null, tools_called: ["create_reservation"] },
      });
      await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
      expect(fake.talkToAgent).toHaveBeenLastCalledWith("la-graine-verte", {
        message: "Yes, please go ahead.",
        conversation: "c-1",
        confirm: "p-1",
      });
      await screen.findByText("It's booked.");
      expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    });

    it("shows a refusal's frames, and the next message starts a new conversation", async () => {
      const fake = client();
      fake.talkToAgent.mockResolvedValueOnce(proposed).mockResolvedValueOnce({
        request: '{"type":"request","id":"r-2","intent":"TALK"}',
        response: '{"type":"response","id":"r-2","status":404}',
        status: 404,
      });
      const { container } = canvas({ view: "agent", client: fake });
      const box = screen.getByLabelText("Ask your agent");
      await userEvent.type(box, "One");
      await userEvent.click(screen.getByRole("button", { name: "Ask" }));
      await screen.findByText(answer.reply);
      await userEvent.type(box, "Two");
      await userEvent.click(screen.getByRole("button", { name: "Ask" }));
      await waitFor(() => expect(container.querySelector(".st-w-s.bad")?.textContent).toBe("404"));
      expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
      await userEvent.clear(box);
      await userEvent.type(box, "Three");
      await userEvent.click(screen.getByRole("button", { name: "Ask" }));
      expect(fake.talkToAgent).toHaveBeenLastCalledWith("la-graine-verte", {
        message: "Three",
        conversation: null,
        confirm: null,
      });
    });

    it("keeps the proposal and the message when the agent is out for a moment", async () => {
      const fake = client();
      fake.talkToAgent.mockResolvedValueOnce(proposed).mockResolvedValueOnce({
        request: '{"type":"request","id":"r-2","intent":"TALK"}',
        response: '{"type":"response","id":"r-2","status":503}',
        status: 503,
      });
      canvas({ view: "agent", client: fake });
      const box = screen.getByLabelText("Ask your agent") as HTMLInputElement;
      await userEvent.type(box, "A table for 2?");
      await userEvent.click(screen.getByRole("button", { name: "Ask" }));
      await screen.findByRole("button", { name: "Confirm" });
      await userEvent.type(box, "And parking?");
      await userEvent.click(screen.getByRole("button", { name: "Ask" }));
      await waitFor(() => expect(fake.talkToAgent).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(box.value).toBe("And parking?"));
      expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
    });

    it("turns a pasted tab into a space", async () => {
      const fake = client();
      canvas({ view: "agent", client: fake });
      const box = screen.getByLabelText("Ask your agent");
      await userEvent.click(box);
      await userEvent.paste("Vegetarian\tdishes?");
      await userEvent.click(screen.getByRole("button", { name: "Ask" }));
      expect(fake.talkToAgent).toHaveBeenCalledWith("la-graine-verte", {
        message: "Vegetarian dishes?",
        conversation: null,
        confirm: null,
      });
    });

    it("keeps an empty message from being sent", async () => {
      const fake = client();
      canvas({ view: "agent", client: fake });
      await userEvent.type(screen.getByLabelText("Ask your agent"), "   ");
      expect((screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement).disabled).toBe(true);
    });
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

// The management site loads on its first visit (#220), after the frame's `load`: it says when it
// listens, and the moment goes then.
describe("Studio's canvas, the moment in its frame", () => {
  function framed(moment: Moment | null) {
    const view = canvas({ moment });
    const frame = view.container.querySelector("iframe")!;
    const posted = vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => {});
    const post = (init: MessageEventInit) =>
      act(() => {
        window.dispatchEvent(new MessageEvent("message", { data: MOMENT_READY, ...init }));
      });
    const ready = () => post({ origin: window.location.origin, source: frame.contentWindow });
    const shown = (next: Moment | null) => view.rerender(<CanvasWith moment={next} />);
    return { posted, post, ready, shown };
  }

  it("holds a moment asked for before the page listens, and sends it once it does", () => {
    const { posted, ready, shown } = framed(null);
    shown("after");
    expect(posted).not.toHaveBeenCalled();

    ready();
    expect(posted).toHaveBeenCalledExactlyOnceWith({ type: "sesame:moment", moment: "after" }, window.location.origin);

    shown("during");
    expect(posted).toHaveBeenLastCalledWith({ type: "sesame:moment", moment: "during" }, window.location.origin);
  });

  it("sends nothing to a page on the clock, nor twice the same moment to one document", () => {
    const { posted, ready, shown } = framed(null);
    ready();
    expect(posted).not.toHaveBeenCalled();
    shown("morning");
    shown("morning");
    expect(posted).toHaveBeenCalledOnce();
  });

  // "Reload the page" after an error: the new document starts on the clock again.
  it("sends the moment again to each document that says it listens", () => {
    const { posted, ready } = framed("after");
    ready();
    ready();
    expect(posted).toHaveBeenCalledTimes(2);
    expect(posted).toHaveBeenLastCalledWith({ type: "sesame:moment", moment: "after" }, window.location.origin);
  });

  it("takes READY from no other window", () => {
    const { posted, post } = framed("after");
    post({ origin: window.location.origin, source: window });
    expect(posted).not.toHaveBeenCalled();
  });

  it("takes READY from its frame on no other origin", () => {
    const view = canvas({ moment: "after" });
    const frame = view.container.querySelector("iframe")!;
    const posted = vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => {});
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", { data: MOMENT_READY, origin: "https://elsewhere.example", source: frame.contentWindow }),
      );
    });
    expect(posted).not.toHaveBeenCalled();
  });
});

// The scroll to a lit fact's words (#220): once per fact, not on every render of Studio.
describe("Studio's canvas, the public page's scroll", () => {
  const frames = { request: window.requestAnimationFrame, cancel: window.cancelAnimationFrame };
  const scrolls = vi.fn();
  beforeEach(() => {
    scrolls.mockClear();
    window.requestAnimationFrame = (callback) => {
      callback(0);
      return 1;
    };
    window.cancelAnimationFrame = () => {};
    HTMLElement.prototype.scrollTo = scrolls as unknown as HTMLElement["scrollTo"];
  });
  afterEach(() => {
    window.requestAnimationFrame = frames.request;
    window.cancelAnimationFrame = frames.cancel;
  });

  const smooth = () => scrolls.mock.calls.filter(([options]) => options?.behavior === "smooth");

  it("scrolls to a lit fact once, and not again when Studio draws again", () => {
    const view = canvas({ view: "public", pages: [HOME, HOURS], fact: FACT });
    expect(smooth()).toHaveLength(1);
    // Studio draws again (a keystroke used to): new callbacks, the same fact.
    view.rerender(<CanvasWith view="public" pages={[HOME, HOURS]} fact={FACT} onView={() => {}} onMoment={() => {}} />);
    expect(smooth()).toHaveLength(1);

    view.rerender(<CanvasWith view="public" pages={[HOME, HOURS]} fact={{ ...FACT, key: "hours", label: "Hours" }} />);
    expect(smooth()).toHaveLength(2);
  });
});
