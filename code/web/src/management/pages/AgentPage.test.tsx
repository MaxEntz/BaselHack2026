import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import fixture from "../../../../tests/fixtures/demo/la-graine-verte-management.json" with { type: "json" };
import { createMockManagementClient } from "../../mocks/managementClient.ts";
import type { ManagementFixture } from "../../types.ts";
import type { ManagementClient } from "../client.ts";
import { ClientContext } from "../data.ts";
import { RefreshContext, createRefreshStore } from "../live.ts";
import { ToastProvider } from "../ui/index.ts";
import { AgentPage, PAUSED_TOAST, RESUMED_TOAST } from "./AgentPage.tsx";

const SLUG = "la-graine-verte";
const demo = fixture as ManagementFixture;

function mock(changes: Partial<ManagementFixture> = {}) {
  return createMockManagementClient({ ...demo, ...changes }, { latencyMs: 0 });
}

function page(client: ManagementClient, version = 0) {
  return (
    <ClientContext.Provider value={client}>
      <ToastProvider>
        <AgentPage slug={SLUG} version={version} />
      </ToastProvider>
    </ClientContext.Provider>
  );
}

const rows = () => document.querySelectorAll<HTMLDetailsElement>("details.item");
const click = (name: string) => fireEvent.click(screen.getByRole("button", { name }));

describe("Agent", () => {
  it("shows the status, both addresses and today's 10 latest requests", async () => {
    render(page(mock()));

    expect(await screen.findByText("Live, and taking bookings.")).toBeTruthy();
    expect(screen.getByText("batp://191-96-88-152.sslip.io/la-graine-verte")).toBeTruthy();
    expect(screen.getByText("https://191-96-88-152.sslip.io/m/la-graine-verte/mcp")).toBeTruthy();
    await waitFor(() => expect(rows()).toHaveLength(10));
    const newest = demo.agent_requests[0];
    const first = rows()[0];
    expect(first.querySelector("time")?.textContent).toBe(newest.at.slice(11, 16));
    expect(within(first).getByText(newest.title)).toBeTruthy();
    expect(within(first).getByText("See the request")).toBeTruthy();
  });

  it("shows the frames as sent when a row opens, and marks refused rows", async () => {
    render(page(mock()));
    await waitFor(() => expect(rows()).toHaveLength(10));
    const refused = demo.agent_requests.find((r) => r.refused);
    const row = [...rows()].find((r) => r.textContent?.includes(refused?.title ?? "?"));

    expect(row?.className).toBe("item ref");
    expect(row?.querySelector("pre")?.textContent).toBe(`→ ${refused?.request}\n← ${refused?.response}`);
  });

  it("filters the refused requests", async () => {
    render(page(mock()));
    await waitFor(() => expect(rows()).toHaveLength(10));

    click("Refused");

    const refusedToday = demo.agent_requests.filter((r) => r.refused && r.at.startsWith("2026-10-31"));
    await waitFor(() => expect(rows()).toHaveLength(Math.min(refusedToday.length, 10)));
    expect([...rows()].every((r) => r.className === "item ref")).toBe(true);
    expect(screen.getByRole("button", { name: "Refused" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("keeps an opened row open when the list is read again", async () => {
    const client = mock();
    const { rerender } = render(page(client));
    await waitFor(() => expect(rows()).toHaveLength(10));
    const second = rows()[1];
    second.open = true;
    fireEvent(second, new Event("toggle"));

    const read = vi.spyOn(client, "agentRequests");
    rerender(page(client, 1));
    await waitFor(() => expect(read).toHaveBeenCalled());

    await waitFor(() => expect(rows()[1].open).toBe(true));
    expect([...rows()].filter((r) => r.open)).toHaveLength(1);
  });

  it("holds the live refresh while a row is open (#137)", async () => {
    const store = createRefreshStore();
    render(<RefreshContext.Provider value={store}>{page(mock())}</RefreshContext.Provider>);
    await waitFor(() => expect(rows()).toHaveLength(10));
    const second = rows()[1];
    second.open = true;
    fireEvent(second, new Event("toggle"));

    act(() => store.bump());
    expect(store.applied()).toBe(0);

    second.open = false;
    fireEvent(second, new Event("toggle"));
    await waitFor(() => expect(store.applied()).toBe(1));
  });

  it("pauses and resumes the agent", async () => {
    const client = mock();
    render(page(client));
    await screen.findByText("Live, and taking bookings.");

    click("Pause the agent");
    expect(await screen.findByText(PAUSED_TOAST)).toBeTruthy();
    expect(screen.getByText("Paused. Guests get your booking page.")).toBeTruthy();
    expect((await client.merchant(SLUG)).status).toBe("down");

    click("Resume the agent");
    expect(await screen.findByText(RESUMED_TOAST)).toBeTruthy();
    expect(screen.getByText("Live, and taking bookings.")).toBeTruthy();
  });

  it("has no switch before the agent is published", async () => {
    render(page(mock({ merchant: { ...demo.merchant, status: "building" } })));

    expect(await screen.findByText("Not published yet. Ask the assistant to publish it.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /the agent$/ })).toBeNull();
  });

  it("copies an address, or shows it when the clipboard is refused", async () => {
    const writeText = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error("denied"));
    Object.assign(navigator, { clipboard: { writeText } });
    render(page(mock()));
    await screen.findByText("Live, and taking bookings.");

    click("Copy its address");
    expect(await screen.findByText("Copied")).toBeTruthy();
    click("Copy for claude");
    expect(await screen.findByText(`Copy this: ${demo.merchant.mcp_url}`)).toBeTruthy();
    expect(writeText).toHaveBeenNthCalledWith(1, demo.merchant.agent_address);
  });
});
