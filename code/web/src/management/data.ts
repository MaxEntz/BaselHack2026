import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ManagementClient } from "./client.ts";
import { managementClient } from "./index.ts";
import { errorMessage } from "./problems.ts";

/** The pages' client: the platform, or the mock with VITE_MOCK=1. Tests provide their own. */
export const ClientContext = createContext<ManagementClient>(managementClient);

export function useClient(): ManagementClient {
  return useContext(ClientContext);
}

/**
 * What `load` answers, read once per `load` (memoize it) and again when `version` changes.
 * `update` changes it, with the answer of a save.
 */
export function useLoad<T>(load: (signal: AbortSignal) => Promise<T>, version = 0) {
  const [state, setState] = useState<{ data?: T; error?: string }>({});
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal).then(
      (data) => setState({ data }),
      (error: unknown) => {
        if (!controller.signal.aborted) setState({ error: errorMessage(error) });
      },
    );
    return () => controller.abort();
  }, [load, version]);
  const update = useCallback(
    (change: (data: T) => T) =>
      setState((state) => (state.data === undefined ? state : { data: change(state.data) })),
    [],
  );
  return { ...state, update };
}
