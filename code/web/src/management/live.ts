// When the management pages read their data again (#137). Two counters:
// `latest` bumps on every change (a guest's live event, a mutation of the site,
// a tool of the assistant); `applied` follows it, except while something holds
// the page: a dialog, an expanded log row, an unsaved form. The pages read
// `applied`, so a background change never replaces what the merchant is using;
// the shell's own reads (brand, bell) follow `latest`.
import { createContext, useContext, useEffect, useSyncExternalStore } from "react";

export type RefreshStore = {
  latest: () => number;
  applied: () => number;
  // Something changed: read again, now or once nothing holds the page.
  bump: () => void;
  // Holds `applied` until the returned release runs (once).
  hold: () => () => void;
  subscribe: (listener: () => void) => () => void;
};

export function createRefreshStore(): RefreshStore {
  let latest = 0;
  let applied = 0;
  let holds = 0;
  const listeners = new Set<() => void>();
  const emit = () => listeners.forEach((listener) => listener());

  return {
    latest: () => latest,
    applied: () => applied,
    bump() {
      latest += 1;
      if (holds === 0) applied = latest;
      emit();
    },
    hold() {
      holds += 1;
      let released = false;
      return () => {
        if (released) return;
        released = true;
        holds -= 1;
        if (holds === 0 && applied !== latest) {
          applied = latest;
          emit();
        }
      };
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

// Outside the shell (the primitives gallery, a page's own test) nothing refreshes.
const idle = createRefreshStore();

export const RefreshContext = createContext<RefreshStore>(idle);

/** The version the pages read: bumps on changes nothing holds back. */
export function usePageVersion(): number {
  const store = useContext(RefreshContext);
  return useSyncExternalStore(store.subscribe, store.applied);
}

/** Every change, held or not: for what can't replace the merchant's work (brand, bell). */
export function useLatestVersion(): number {
  const store = useContext(RefreshContext);
  return useSyncExternalStore(store.subscribe, store.latest);
}

/** While `active`, background changes wait: a dialog is open, a row expanded, a form unsaved. */
export function useHoldRefresh(active: boolean): void {
  const store = useContext(RefreshContext);
  useEffect(() => (active ? store.hold() : undefined), [active, store]);
}
