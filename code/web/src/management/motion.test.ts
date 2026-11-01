import { describe, expect, it, vi } from "vitest";
import { leaveTransitionsToParent } from "./motion.ts";

function page() {
  const document = window.document.implementation.createHTMLDocument();
  Object.defineProperty(document, "startViewTransition", { value: vi.fn(), configurable: true });
  return document as Document & { startViewTransition?: unknown };
}

describe("leaveTransitionsToParent", () => {
  it("removes the transitions of a page in a frame", () => {
    const document = page();
    leaveTransitionsToParent({ parent: window, document });
    expect(document.startViewTransition).toBeUndefined();
  });

  it("leaves a page on its own alone", () => {
    const document = page();
    const alone = { document } as { document: Document; parent: Window };
    alone.parent = alone as unknown as Window;
    leaveTransitionsToParent(alone);
    expect(document.startViewTransition).toBeTypeOf("function");
  });
});
