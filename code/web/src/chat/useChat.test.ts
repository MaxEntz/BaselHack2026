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

  it("keeps the chat busy for a turn sent right after a reset, when the old turn ends (#220)", async () => {
    // Studio's examples reset the chat and send in the same tick.
    const sent: string[] = [];
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    const racing: ChatClient = {
      ...client,
      async *send(message, _session, signal) {
        sent.push(message);
        yield { type: "tool_start", tool: "list_reservations", label: "Checking tonight's bookings" };
        if (message === "old") {
          await new Promise((_, reject) =>
            signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError"))),
          );
        }
        if (message === "new") await held;
        yield { type: "text", delta: `${message} done` };
        yield { type: "done" };
      },
    };
    const { result } = renderHook(() => useChat(racing, { onboarding: "", operations: "" }, null));

    let old: Promise<void> = Promise.resolve();
    act(() => {
      old = result.current.send("old");
    });
    await vi.waitFor(() => expect(sent).toEqual(["old"]));
    let next: Promise<void> = Promise.resolve();
    act(() => {
      result.current.reset("");
      next = result.current.send("new");
    });
    await act(() => old);

    // The old turn has ended, the new one still runs: a third message waits for it.
    await act(() => result.current.send("third"));
    expect(sent).toEqual(["old", "new"]);
    expect(result.current.state.busy).toBe(true);

    release();
    await act(() => next);
    expect(result.current.state.busy).toBe(false);
    expect(JSON.stringify(result.current.state.items)).toContain("new done");
    expect(JSON.stringify(result.current.state.items)).not.toContain("old done");
    await act(() => result.current.send("third"));
    expect(sent).toEqual(["old", "new", "third"]);
  });
});
