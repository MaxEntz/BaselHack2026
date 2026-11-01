import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import onboarding from "../mocks/onboarding.json";
import type { ChatEvent } from "../types.ts";
import { CardView } from "./CardView.tsx";
import { merchantMcpUrl } from "./format.ts";
import { StepsCard } from "./StepsCard.tsx";

const PROFILE = (onboarding as ChatEvent[]).find(
  (event) => event.type === "card" && event.kind === "profile",
);

describe("profile card", () => {
  it("shows the owner's secret link once, after a build (#132)", () => {
    if (PROFILE?.type !== "card" || PROFILE.kind !== "profile") throw new Error("no profile card");

    render(<CardView card={PROFILE} />);

    expect(screen.getByText(/shown only once/)).toBeTruthy();
    expect(screen.getByText(PROFILE.login_url ?? "missing")).toBeTruthy();
  });

  it("says Copied for a while, and leaves no timer behind once gone (#220)", async () => {
    if (PROFILE?.type !== "card" || PROFILE.kind !== "profile") throw new Error("no profile card");
    vi.useFakeTimers();
    const clipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    });
    try {
      const { unmount } = render(<CardView card={PROFILE} />);
      const [first] = screen.getAllByRole("button", { name: "Copy" });
      await act(async () => fireEvent.click(first));
      expect(screen.getByRole("button", { name: "Copied ✓" })).toBeTruthy();
      act(() => vi.advanceTimersByTime(2000));
      expect(screen.queryByRole("button", { name: "Copied ✓" })).toBeNull();

      await act(async () => fireEvent.click(screen.getAllByRole("button", { name: "Copy" })[0]));
      unmount();
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
      if (clipboard) Object.defineProperty(navigator, "clipboard", clipboard);
      else Reflect.deleteProperty(navigator, "clipboard");
    }
  });

  it("gives the restaurant's own MCP URL next to the gateway's, as the platform does", () => {
    expect(merchantMcpUrl("http://localhost:8001/mcp", "la-graine-verte")).toBe(
      "http://localhost:8001/m/la-graine-verte/mcp",
    );
    expect(merchantMcpUrl("https://example.org/gateway/mcp", "le-petit-zinc")).toBe(
      "https://example.org/gateway/m/le-petit-zinc/mcp",
    );
  });

  it("shows no link when the card has none", () => {
    if (PROFILE?.type !== "card" || PROFILE.kind !== "profile") throw new Error("no profile card");

    render(<CardView card={{ ...PROFILE, login_url: null }} />);

    expect(screen.queryByText(/shown only once/)).toBeNull();
  });
});

describe("review card (#136)", () => {
  it("says the agent is hidden, shows each fact with its source, and the flags", () => {
    if (PROFILE?.type !== "card" || PROFILE.kind !== "profile") throw new Error("no profile card");

    render(<CardView card={PROFILE} />);

    expect(screen.getByText(/Hidden from guests until you publish/)).toBeTruthy();
    expect(screen.getByText(/4, from "Tables de 1 à 4 personnes"/)).toBeTruthy();
    expect(screen.getByText(/^\/#reserver, used as the fallback/)).toBeTruthy();
    expect(screen.getByText("Your site only takes bookings by phone.")).toBeTruthy();
  });

  it("titles a build waiting for its owner, and a publish on its own", () => {
    const { rerender } = render(
      <StepsCard
        steps={{
          crawl: { status: "done", duration_ms: 800 },
          extract: { status: "done", duration_ms: 1400 },
          validate: { status: "done", duration_ms: 4 },
          provision: { status: "done", duration_ms: 110 },
          deploy: { status: "waiting", detail: "Waiting for you." },
        }}
      />,
    );
    expect(screen.getByText(/check it, then publish/)).toBeTruthy();
    expect(screen.getByText("Waiting for you.")).toBeTruthy();

    rerender(<StepsCard steps={{ deploy: { status: "done", detail: "Your agent is live." } }} />);
    expect(screen.getByText("Your agent is live")).toBeTruthy();
    expect(screen.queryByText("Reading your website")).toBeNull();
  });
});

describe("change cards (#152)", () => {
  it("strikes the old value and bolds the new one", () => {
    const { container } = render(
      <CardView
        card={{
          type: "card",
          kind: "diff",
          data: { label: "Cancelling", before: "None", after: "Free up to 2 hours before", view: null },
        }}
      />,
    );

    expect(container.querySelector("s")?.textContent).toBe("None");
    expect(container.querySelector("b")?.textContent).toBe("Free up to 2 hours before");
  });

  it("shows nothing for a view card in the plain chat", () => {
    const { container } = render(
      <CardView card={{ type: "card", kind: "view", data: { view: "agent" } }} />,
    );

    expect(container.textContent).toBe("");
  });
});
