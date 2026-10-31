import { type FormEvent, useState } from "react";
import { strings } from "../strings.ts";
import { useHoldRefresh } from "./live.ts";
import { errorMessage } from "./problems.ts";
import { useDraft, useToast } from "./ui/index.ts";

/**
 * A page form over `initial`: Save sends the draft through `save` and keeps what the server
 * answered; Discard restores the saved values. A refused rule shows in the save bar, its
 * services named by `names` from the saved value. Unsaved changes hold the live refresh (#137);
 * without any, the form takes `initial` again when a change elsewhere (the assistant) alters it.
 */
export function useSavedForm<T>(
  initial: T,
  save: (draft: T) => Promise<T>,
  names?: (saved: T) => readonly string[],
) {
  const draft = useDraft(initial);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  useHoldRefresh(draft.dirty);
  const fresh = JSON.stringify(initial);
  const [read, setRead] = useState(fresh);
  if (fresh !== read) {
    setRead(fresh);
    if (!draft.dirty) draft.commit(initial);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      draft.commit(await save(draft.value));
      toast(
        <>
          <b>Saved</b>
          {strings.rulesSaved}
        </>,
      );
    } catch (failure) {
      setError(errorMessage(failure, names?.(draft.saved)));
    } finally {
      setSaving(false);
    }
  }

  function discard() {
    draft.reset();
    setError(null);
  }

  return {
    saved: draft.saved,
    value: draft.value,
    set: draft.set,
    dirty: draft.dirty,
    error,
    submit,
    discard,
  };
}
