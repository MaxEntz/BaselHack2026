// "Nothing pops in" (#137): new content crossfades through the View Transitions
// API. Without the API, or with reduced motion, it just appears.
import { flushSync } from "react-dom";

type WithTransitions = Document & { startViewTransition?: (update: () => void) => unknown };

export function motionAllowed(): boolean {
  const doc = document as WithTransitions;
  if (typeof doc.startViewTransition !== "function") return false;
  return !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

/** Runs `update` (a React state change) inside a crossfade when motion is allowed. */
export function crossfade(update: () => void): void {
  if (!motionAllowed()) {
    update();
    return;
  }
  (document as WithTransitions).startViewTransition?.(() => flushSync(update));
}
