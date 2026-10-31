import { useState } from "react";

// Ids that appeared since the previous read: their rows get the `.fresh` highlight (#137).
// Computed while rendering (React's "adjust state on a prop change"), so a new row is
// highlighted on the same render that shows it, never one frame later. A new `scope` (another
// day) starts over: what it shows first isn't new.
type FreshState = { scope: string; key: string | null; seen: Set<string> | null; fresh: Set<string> };

export function useFresh(ids: readonly string[] | null, scope = ""): Set<string> {
  const key = ids?.join("\n") ?? null;
  const [state, setState] = useState<FreshState>({ scope, key: null, seen: null, fresh: new Set() });
  if (key === null || (key === state.key && scope === state.scope)) return state.fresh;
  const current = key === "" ? [] : key.split("\n");
  const before = scope === state.scope ? state.seen : null;
  const next: FreshState = {
    scope,
    key,
    seen: new Set(current),
    fresh: before ? new Set(current.filter((id) => !before.has(id))) : new Set(),
  };
  setState(next);
  return next.fresh;
}
