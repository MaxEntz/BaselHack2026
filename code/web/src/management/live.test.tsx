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

describe("changes", () => {
  afterEach(() => vi.restoreAllMocks());

  it("reach the listeners of their merchant only", () => {
    const mine = vi.fn();
    const other = vi.fn();
    const stop = onChange("la-graine-verte", mine);
    onChange("le-petit-zinc", other)();
    announceChange("la-graine-verte");
    announceChange("le-petit-zinc");
    stop();
    announceChange("la-graine-verte");
    expect(mine).toHaveBeenCalledTimes(1);
    expect(other).not.toHaveBeenCalled();
  });

  it("are announced after a mutation that succeeds, not after a read or a refusal", async () => {
    const heard = vi.fn();
    const stop = onChange("s", heard);
    const client = announcingChanges({
      rules: vi.fn(async () => ({})),
      saveRules: vi.fn(async () => ({ saved: true })),
      cancelBooking: vi.fn(async () => {
        throw new Error("refused");
      }),
    } as unknown as ManagementClient);
    await client.rules("s");
    expect(heard).not.toHaveBeenCalled();
    expect(await client.saveRules("s", {} as never)).toEqual({ saved: true });
    expect(heard).toHaveBeenCalledTimes(1);
    await expect(client.cancelBooking("s", "R-001")).rejects.toThrow("refused");
    expect(heard).toHaveBeenCalledTimes(1);
    stop();
  });
});
