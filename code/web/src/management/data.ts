import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ManagementClient } from "./client.ts";
import { managementClient } from "./index.ts";
import { crossfade } from "./motion.ts";
import { errorMessage } from "./problems.ts";

/** The pages' client: the platform, or the mock with VITE_MOCK=1. Tests provide their own. */
export const ClientContext = createContext<ManagementClient>(managementClient);

export function useClient(): ManagementClient {
  return useContext(ClientContext);
}

/**
 * What `load` answers, read once per `load` (memoize it) and again when `version` changes.
 * `update` changes it, with the answer of a save. A read again for a new `version` crossfades
 * into the page (#137), unless `animate` is false; one for a new `load` (a search typed) just
 * replaces it.
 */
export function useLoad<T>(load: (signal: AbortSignal) => Promise<T>, version = 0, animate = true) {
  const [state, setState] = useState<{ data?: T; error?: string }>({});
  const shown = useRef<{ version: number } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal).then(
      (data) => {
        if (controller.signal.aborted) return;
        const live = animate && shown.current !== null && shown.current.version !== version;
        shown.current = { version };
        const apply = () => setState({ data });
        if (live) crossfade(apply);
        else apply();
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setState({ error: errorMessage(error) });
      },
    );
    return () => controller.abort();
  }, [load, version, animate]);
  const update = useCallback(
    (change: (data: T) => T) =>
      setState((state) => (state.data === undefined ? state : { data: change(state.data) })),
    [],
  );
  return { ...state, update };
}
