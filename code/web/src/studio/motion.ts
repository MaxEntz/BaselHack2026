import { useLayoutEffect, useRef, type RefObject } from "react";
import { flushSync } from "react-dom";

const calm = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

const CHAT_MIN = 380;
const CHAT_SHARE = 0.4;
const RAIL = 76;
const NARROW = 900;

// Nothing appears in one frame: the change crossfades, and named elements travel to their new
// place. Without the API, or with reduced motion, it just happens.
export function crossfade(change: () => void): void {
  if (calm() || !document.startViewTransition) {
    change();
    return;
  }
  document.startViewTransition(() => {
    flushSync(change);
  });
}

// Opening the canvas: the chat column narrows from the full width to its share, and the canvas,
// held at its final width, slides in from the right. Afterwards, the stylesheet's grid takes over.
export function useSplitOpening(
  open: boolean,
  body: RefObject<HTMLElement | null>,
  canvas: RefObject<HTMLElement | null>,
): void {
  const wasOpen = useRef(open);
  useLayoutEffect(() => {
    const opening = open && !wasOpen.current;
    wasOpen.current = open;
    const grid = body.current;
    const pane = canvas.current;
    if (!opening || !grid || !pane || calm() || !grid.animate) return;
    const width = grid.clientWidth;
    if (window.innerWidth <= NARROW) return;
    const chat = Math.max(CHAT_MIN, width * CHAT_SHARE);
    const final = width - RAIL - chat;
    const timing = { duration: 620, easing: "cubic-bezier(.3,0,.1,1)" };
    const moves = [
      grid.animate(
        {
          gridTemplateColumns: [
            `${RAIL}px ${width - RAIL}px 0px`,
            `${RAIL}px ${chat}px ${final}px`,
          ],
        },
        timing,
      ),
      pane.animate({ width: [`${final}px`, `${final}px`] }, timing),
    ];
    return () => moves.forEach((move) => move.cancel());
  }, [open, body, canvas]);
}
