import type { KeyboardEvent } from "react";
import { describe, expect, it } from "vitest";
import { composing } from "./ime.ts";

const enter = (isComposing: boolean, keyCode: number) =>
  ({ key: "Enter", keyCode, nativeEvent: { isComposing } }) as unknown as KeyboardEvent;

describe("an Enter during an IME composition (#220)", () => {
  it("is the composition's, as browsers flag it", () => {
    expect(composing(enter(true, 13))).toBe(true);
  });

  it("is the composition's too in Safari, which only sends keyCode 229", () => {
    expect(composing(enter(false, 229))).toBe(true);
  });

  it("is a send otherwise", () => {
    expect(composing(enter(false, 13))).toBe(false);
  });
});
