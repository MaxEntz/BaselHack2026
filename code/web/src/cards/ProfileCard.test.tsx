import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import onboarding from "../mocks/onboarding.json";
import type { ChatEvent } from "../types.ts";
import { CardView } from "./CardView.tsx";

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

  it("shows no link when the card has none", () => {
    if (PROFILE?.type !== "card" || PROFILE.kind !== "profile") throw new Error("no profile card");

    render(<CardView card={{ ...PROFILE, login_url: null }} />);

    expect(screen.queryByText(/shown only once/)).toBeNull();
  });
});
