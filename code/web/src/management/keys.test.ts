import { afterEach, describe, expect, it, vi } from "vitest";
import { newKey } from "./keys.ts";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("idempotency keys", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("are v4 UUIDs, also where randomUUID is missing (plain HTTP)", () => {
    expect(newKey()).toMatch(UUID);
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
    const keys = new Set(Array.from({ length: 50 }, newKey));
    expect(keys.size).toBe(50);
    for (const key of keys) expect(key).toMatch(UUID);
  });
});
