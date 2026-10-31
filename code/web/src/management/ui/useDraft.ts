import { useCallback, useState } from "react";

/**
 * A form's draft: `dirty` once it differs from what was loaded; `reset` discards it.
 * `commit` makes the draft the saved value, or `next` when the server answered its own.
 */
export function useDraft<T>(initial: T) {
  const [saved, setSaved] = useState(initial);
  const [value, setValue] = useState(initial);
  const reset = useCallback(() => setValue(saved), [saved]);
  const commit = useCallback(
    (next: T = value) => {
      setSaved(next);
      setValue(next);
    },
    [value],
  );
  const dirty = JSON.stringify(value) !== JSON.stringify(saved);
  return { saved, value, set: setValue, dirty, reset, commit };
}
