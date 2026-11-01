import { useCallback, useEffect, useState } from "react";
import { copyText } from "../clipboard.ts";

const COPIED_MS = 2200;

/** The copy buttons: `copy` copies, and `toast` says how it went for a moment. */
export function useCopy(): { toast: string | null; copy: (text: string) => void } {
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const copy = useCallback((text: string) => {
    void copyText(text).then(({ message }) => setToast(message));
  }, []);

  return { toast, copy };
}
