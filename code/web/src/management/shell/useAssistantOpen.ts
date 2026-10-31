import { useEffect, useState } from "react";

// Wide enough for the assistant beside the page (the mockup's ≤1240 px breakpoint).
const WIDE = "(width > 1240px)";

/**
 * Whether the assistant's column is open: at first where there's room, then following every
 * resize across 1240 px (the mockup only checks at load).
 */
export function useAssistantOpen(): [boolean, (open: boolean) => void] {
  const [open, setOpen] = useState(() => window.matchMedia?.(WIDE).matches ?? false);
  useEffect(() => {
    const query = window.matchMedia?.(WIDE);
    if (!query) return;
    const follow = (event: MediaQueryListEvent) => setOpen(event.matches);
    query.addEventListener("change", follow);
    return () => query.removeEventListener("change", follow);
  }, []);
  return [open, setOpen];
}
