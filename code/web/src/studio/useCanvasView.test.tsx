import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChatItem } from "../chat/state.ts";
import { useCanvasView } from "./useCanvasView.ts";

const WELCOME: ChatItem = { id: "welcome", turn: 0, type: "assistant", text: "Hello" };

// The assistant's `show_view` card (#152), with the id the reducer gave it.
const viewCard = (id: string, view: "agent" | "manage"): ChatItem => ({
  id,
  turn: 1,
  type: "card",
  card: { type: "card", kind: "view", data: { view } },
});

const input = (items: readonly ChatItem[]) => ({ items, profile: null, pageCount: 0, firstPage: false });

describe("useCanvasView", () => {
  it("shows the view a card asks for again in a new conversation that reuses its id", () => {
    const { result, rerender } = renderHook((props) => useCanvasView(props), { initialProps: input([WELCOME]) });
    expect(result.current.view).toBe("manage");

    rerender(input([WELCOME, viewCard("3", "agent")]));
    expect(result.current.view).toBe("agent");
    expect(result.current.pane).toBe("canvas");

    // Start over: the reducer counts ids from 1 again (chat/state.ts `reset`).
    act(() => result.current.clear());
    rerender(input([WELCOME]));
    expect(result.current.view).toBe("manage");

    rerender(input([WELCOME, viewCard("3", "agent")]));
    expect(result.current.view).toBe("agent");
    expect(result.current.pane).toBe("canvas");
  });

  it("switches once per card, not on every render", () => {
    const items = [WELCOME, viewCard("3", "agent")];
    const { result, rerender } = renderHook((props) => useCanvasView(props), { initialProps: input(items) });
    expect(result.current.view).toBe("agent");

    act(() => result.current.showView("manage"));
    rerender(input([...items]));
    expect(result.current.view).toBe("manage");
  });
});
