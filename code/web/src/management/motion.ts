// "Nothing pops in" (#137): new content crossfades through the View Transitions
// API. Without the API, or with reduced motion, it just appears.
import { flushSync } from "react-dom";

type WithTransitions = Document & { startViewTransition?: (update: () => void) => unknown };

export function motionAllowed(): boolean {
  const doc = document as WithTransitions;
  if (typeof doc.startViewTransition !== "function") return false;
  return !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Studio shows this site in a frame, and a transition in each at once crashes Chromium's compositor:
 * the tab died on 3 Publish out of 5. In a frame, only the page around it animates. Removing the API,
 * not checking it at each call, also stops the router's own transitions.
 */
export function leaveTransitionsToParent(win: Pick<Window, "parent" | "document"> & object = window): void {
  if (win.parent === win) return;
  Object.defineProperty(win.document, "startViewTransition", { value: undefined, configurable: true });
}

/** Runs `update` (a React state change) inside a crossfade when motion is allowed. */
export function crossfade(update: () => void): void {
  if (!motionAllowed()) {
    update();
    return;
  }
  (document as WithTransitions).startViewTransition?.(() => flushSync(update));
}
