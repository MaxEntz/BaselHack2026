import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import opening from "../../../../tests/fixtures/demo/la-graine-verte-assistant.json" with { type: "json" };
import fixtureJson from "../../../../tests/fixtures/demo/la-graine-verte-management.json" with { type: "json" };
import type { ChatClient } from "../../chat/client.ts";
import { createMockManagementClient } from "../../mocks/managementClient.ts";
import { ManagePage } from "../ManagePage.tsx";
import { routesWith } from "../../router.tsx";
import type { ChatEvent, ConversationItem, ManagementFixture } from "../../types.ts";
import type { ManagementClient } from "../client.ts";
import { ClientContext } from "../data.ts";
import { ChatClientContext } from "./chatClient.ts";

// The management site drawn at once: the app loads it on its first visit (router.tsx).
const routes = routesWith(<ManagePage />);

const fixture = fixtureJson as ManagementFixture;
const SLUG = "la-graine-verte";
const TODAY = fixture.now.slice(0, 10);

function management(conversation: ConversationItem[] = []) {
  return createMockManagementClient(fixture, { latencyMs: 0, liveBookingDelayMs: 60_000, conversation });
}

// A model that answers each message with `turn(message)`, recording what it was sent.
function scripted(turn: (message: string) => Promise<ChatEvent[]> | ChatEvent[]) {
  const sent: string[] = [];
  const client: ChatClient = {
    async *send(message) {
      sent.push(message);
      for (const event of await turn(message)) yield event;
      yield { type: "done" };
    },
    subscribe: () => () => {},
    session: async () => ({ merchant: { slug: SLUG, name: "La Graine Verte" } }),
    claim: async () => {},
  };
  return { client, sent };
}

function at(path: string, client: ManagementClient, chat: ChatClient) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <ClientContext.Provider value={client}>
      <ChatClientContext.Provider value={chat}>
        <RouterProvider router={router} />
      </ChatClientContext.Provider>
    </ClientContext.Provider>,
  );
  return router;
}

// Wide enough for the assistant beside the page.
beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("1240"),
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
});
afterEach(() => vi.unstubAllGlobals());

const panel = () => screen.getByRole("complementary", { name: "Assistant" });
const box = () => within(panel()).getByRole("textbox", { name: "Ask anything about your restaurant" });
const send = () => within(panel()).getByRole("button", { name: "Send" });

describe("assistant panel", () => {
  it("shows the conversation the platform kept, in the management look", async () => {
    const { client } = scripted(() => []);
    at(`/manage/${SLUG}/rules`, management(opening.opening as ConversationItem[]), client);

    await within(panel()).findByText("How busy are we tomorrow?");
    const log = within(panel()).getByRole("log", { name: "Conversation" });
    expect(log.querySelector(".ping")?.textContent).toBe(
      "New booking through your agentSara Muller, 2 guests, tonight 18:30, T1",
    );
    expect(log.querySelector(".msg.me")?.textContent).toBe("How busy are we tomorrow?");
    expect(within(log).getByText("20 guests").tagName).toBe("STRONG");
  });

  it("offers the first 4 chips not asked yet, and sends one on click", async () => {
    const user = userEvent.setup();
    const { client, sent } = scripted(() => [{ type: "text", delta: "Yes." }]);
    at(`/manage/${SLUG}/rules`, management(opening.opening as ConversationItem[]), client);

    const chips = () => within(panel()).getAllByRole("button").filter((b) => b.classList.contains("pill"));
    // The fixture's clock is Saturday 19:40: "tonight" and "Sunday" are still ahead.
    await waitFor(() =>
      expect(chips().map((b) => b.textContent)).toEqual([
        "Table for 4 at 20:00 tonight",
        "Bookings for Sunday lunch",
        "What are my opening hours?",
        "Is my agent live?",
      ]),
    );
    await user.click(chips()[0]);
    expect(sent).toEqual(["Table for 4 at 20:00 tonight"]);
    await within(panel()).findByText("Yes.");
    expect(chips().map((b) => b.textContent)).not.toContain("Table for 4 at 20:00 tonight");
  });

  it("sends on Enter, keeps Shift+Enter for a new line, and never sends nothing", async () => {
    const user = userEvent.setup();
    const { client, sent } = scripted(() => [{ type: "text", delta: "Noted." }]);
    at(`/manage/${SLUG}/rules`, management(), client);

    expect(await within(panel()).findByRole("button", { name: "Send" })).toHaveProperty("disabled", true);
    await user.type(box(), "   ");
    expect(send()).toHaveProperty("disabled", true);
    await user.clear(box());
    await user.type(box(), "First line{Shift>}{Enter}{/Shift}second");
    expect(sent).toEqual([]);
    expect((box() as HTMLTextAreaElement).value).toBe("First line\nsecond");
    await user.type(box(), "{Enter}");
    expect(sent).toEqual(["First line\nsecond"]);
    expect((box() as HTMLTextAreaElement).value).toBe("");
    await within(panel()).findByText("Noted.");
  });
});

describe("assistant panel, links and changes", () => {
  it("links a reply to its page", async () => {
    const user = userEvent.setup();
    const { client } = scripted(() => [
      { type: "text", delta: "Lunch 12:00–14:00, dinner 18:30–22:00." },
      { type: "card", kind: "page", data: { page: "hours" } },
    ]);
    const router = at(`/manage/${SLUG}/rules`, management(), client);

    await user.type(await within(panel()).findByRole("textbox"), "What are my opening hours?{Enter}");
    const link = await within(panel()).findByRole("link", { name: "Opening hours" });
    expect(link.parentElement?.textContent).toBe("You can change this in Opening hours.");
    await user.click(link);
    expect(router.state.location.pathname).toBe(`/manage/${SLUG}/hours`);
  });

  it("opens a listed booking on Reservations", async () => {
    const user = userEvent.setup();
    const { client } = scripted(() => [
      { type: "tool_start", tool: "list_reservations", label: "Checking tonight's bookings…" },
      {
        type: "card",
        kind: "reservations",
        data: {
          date: TODAY,
          reservations: [
            { id: "R-009", time: "18:30", guest_name: "Sara Muller", party_size: 2, table_label: "T1", via: "claude", status: "confirmed" },
          ],
        },
      },
    ]);
    const router = at(`/manage/${SLUG}/rules`, management(), client);

    await user.type(await within(panel()).findByRole("textbox"), "What's on tonight?{Enter}");
    await user.click(await within(panel()).findByRole("button", { name: /Sara Muller/ }));
    expect(router.state.location.pathname + router.state.location.search).toBe(
      `/manage/${SLUG}/reservations?date=${TODAY}&booking=R-009`,
    );
  });

  it("a block from the panel shows on Opening hours without a reload", async () => {
    const user = userEvent.setup();
    const client = management();
    // The model blocks 20:30 tonight through its tool, as the platform's block_slot does.
    const { client: chat } = scripted(async () => {
      await client.setBlocked(SLUG, TODAY, ["20:30"]);
      return [
        { type: "tool_start", tool: "block_slot", label: "Blocking that time…" },
        { type: "card", kind: "confirmation", data: { text: "20:30 is blocked tonight." } },
      ];
    });
    at(`/manage/${SLUG}/hours`, client, chat);
    await screen.findByText("Sun 1 Nov, 13:00, open it again");
    expect(screen.queryByText(/20:30, open it again/)).toBeNull();

    await user.type(await within(panel()).findByRole("textbox"), "Block 20:30 tonight{Enter}");

    await within(panel()).findByText("20:30 is blocked tonight.");
    expect(await screen.findByText("Sat 31 Oct, 20:30, open it again")).toBeTruthy();
  });
});
