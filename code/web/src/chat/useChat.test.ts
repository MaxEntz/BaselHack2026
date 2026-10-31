import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { announceChange } from "../management/changes.ts";
import type { ChatEvent } from "../types.ts";
import type { ChatClient } from "./client.ts";
import { useChat } from "./useChat.ts";

const TURNS: Record<string, ChatEvent[]> = {
  tonight: [
    { type: "tool_start", tool: "list_reservations", label: "Checking tonight's bookings" },
    { type: "card", kind: "reservations", data: { date: "2026-10-31", reservations: [] } },
    { type: "done" },
  ],
  cancelling: [
    { type: "tool_start", tool: "update_rules", label: "Updating your rules" },
    {
      type: "card",
      kind: "diff",
      data: { label: "Cancelling", before: null, after: "Free until 2 h before", view: null },
    },
    { type: "done" },
  ],
};

const client: ChatClient = {
  async *send(message) {
    yield* TURNS[message];
  },
  subscribe: () => () => {},
  session: async () => ({ merchant: null }),
  claim: async () => {},
};

describe("useChat", () => {
  it("announces the change a tool made to the session's merchant, not a read (#137)", async () => {
    const frame = new BroadcastChannel("sesame:changes");
    const heard: unknown[] = [];
    frame.onmessage = (event: MessageEvent) => heard.push(event.data);
    const merchant = { slug: "la-graine-verte", name: "La Graine Verte" };
    const { result } = renderHook(() => useChat(client, { onboarding: "", operations: "" }, merchant));
    await act(() => result.current.send("tonight"));
    announceChange("after-the-read");
    await vi.waitFor(() => expect(heard).toEqual([{ slug: "after-the-read" }]));
    await act(() => result.current.send("cancelling"));
    await vi.waitFor(() => expect(heard).toHaveLength(2));
    expect(heard[1]).toEqual({ slug: "la-graine-verte" });
    frame.close();
  });
});
