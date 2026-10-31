import { useCallback, useRef } from "react";

/**
 * An idempotency key. `crypto.randomUUID` only exists in secure contexts (HTTPS, localhost);
 * `getRandomValues` exists everywhere, so the key is built from it otherwise (a v4 UUID).
 */
export function newKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * One key per payload, kept while a form sends the same one: a retry after a lost answer is
 * the same request to the server, which answers it without booking twice. Another payload
 * gets a new key (the server refuses a key reused with other parameters).
 */
export function useIdempotencyKey(): (payload: unknown) => string {
  const last = useRef<{ payload: string; key: string } | null>(null);
  return useCallback((payload: unknown) => {
    const text = JSON.stringify(payload);
    if (last.current?.payload !== text) last.current = { payload: text, key: newKey() };
    return last.current.key;
  }, []);
}
