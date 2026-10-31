import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { announceChange, announcingChanges, onChange } from "./changes.ts";
import type { ManagementClient } from "./client.ts";
import { RefreshContext, createRefreshStore, useHoldRefresh, usePageVersion } from "./live.ts";

describe("refresh store", () => {
  it("applies every change while nothing holds the page", () => {
    const store = createRefreshStore();
    store.bump();
    store.bump();
    expect([store.latest(), store.applied()]).toEqual([2, 2]);
  });

  it("keeps changes back while held, then applies them once", () => {
    const store = createRefreshStore();
    const listener = vi.fn();
    store.subscribe(listener);
    const dialog = store.hold();
    const row = store.hold();
    store.bump();
    store.bump();
    expect([store.latest(), store.applied()]).toEqual([2, 0]);
    dialog();
    dialog();
    expect(store.applied()).toBe(0);
    listener.mockClear();
    row();
    expect(store.applied()).toBe(2);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("emits nothing on a release when nothing changed", () => {
    const store = createRefreshStore();
    const listener = vi.fn();
    store.subscribe(listener);
    store.hold()();
    expect(listener).not.toHaveBeenCalled();
  });
});

function Page({ holding }: { holding: boolean }) {
  useHoldRefresh(holding);
  return <p>version {usePageVersion()}</p>;
}

describe("useHoldRefresh", () => {
  it("holds the page's version while active", () => {
    const store = createRefreshStore();
    const tree = (holding: boolean) => (
      <RefreshContext.Provider value={store}>
        <Page holding={holding} />
      </RefreshContext.Provider>
    );
    const view = render(tree(true));
    act(() => store.bump());
    expect(screen.getByText("version 0")).toBeTruthy();
    view.rerender(tree(false));
    expect(screen.getByText("version 1")).toBeTruthy();
  });
});

// Another window's channel: what it hears, in order.
function otherWindow() {
  const channel = new BroadcastChannel("sesame:changes");
  const heard: unknown[] = [];
  channel.onmessage = (event: MessageEvent) => heard.push(event.data);
  return { channel, heard };
}

describe("changes", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("reach the listeners of their merchant only, in this window and in the others", async () => {
    const mine = vi.fn();
    const other = vi.fn();
    const stop = onChange("la-graine-verte", mine);
    const stopOther = onChange("le-petit-zinc", other);
    const frame = otherWindow();
    announceChange("la-graine-verte");
    await vi.waitFor(() => expect(frame.heard).toEqual([{ slug: "la-graine-verte" }]));
    await vi.waitFor(() => expect(mine).toHaveBeenCalledTimes(1));
    // Announced in the other window: heard here.
    frame.channel.postMessage({ slug: "le-petit-zinc" });
    await vi.waitFor(() => expect(other).toHaveBeenCalledTimes(1));
    stop();
    announceChange("la-graine-verte");
    announceChange("le-petit-zinc");
    await vi.waitFor(() => expect(other).toHaveBeenCalledTimes(2));
    expect(mine).toHaveBeenCalledTimes(1);
    stopOther();
    frame.channel.close();
  });

  it("stay in this window where there is no BroadcastChannel", () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const heard = vi.fn();
    const stop = onChange("s", heard);
    announceChange("s");
    announceChange("t");
    stop();
    announceChange("s");
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it("are announced after a mutation that succeeds, not after a read or a refusal", async () => {
    const frame = otherWindow();
    const client = announcingChanges({
      rules: vi.fn(async () => ({})),
      saveRules: vi.fn(async () => ({ saved: true })),
      cancelBooking: vi.fn(async () => {
        throw new Error("refused");
      }),
    } as unknown as ManagementClient);
    await client.rules("s");
    expect(await client.saveRules("s", {} as never)).toEqual({ saved: true });
    await expect(client.cancelBooking("s", "R-001")).rejects.toThrow("refused");
    announceChange("end");
    await vi.waitFor(() => expect(frame.heard).toEqual([{ slug: "s" }, { slug: "end" }]));
    frame.channel.close();
  });
});
