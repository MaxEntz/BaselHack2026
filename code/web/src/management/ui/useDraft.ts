import { useCallback, useState } from "react";

/** A form's draft: `dirty` once it differs from what was loaded; `reset` discards it. */
export function useDraft<T>(initial: T) {
  const [saved, setSaved] = useState(initial);
  const [value, setValue] = useState(initial);
  const reset = useCallback(() => setValue(saved), [saved]);
  const commit = useCallback(() => setSaved(value), [value]);
  const dirty = JSON.stringify(value) !== JSON.stringify(saved);
  return { value, set: setValue, dirty, reset, commit };
}
