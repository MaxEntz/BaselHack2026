// How the platform reads a body before any rule: its fields, texts and times (`core.models`,
// core/forms.py). Each refusal is the 422 the platform answers.
import { invalid } from "./context.ts";

// The bounds of `core.models` and core/forms.py.
export const MAX_GUEST_TEXT = 200;
export const MAX_SHORT_TEXT = 80;
export const MAX_POLICY_TEXT = 500;
export const MAX_TABLE_LABEL = 20;
export const MAX_SERVICES = 8;
export const MAX_TABLES = 100;
export const MAX_DAY_STARTS = 48;
export const MAX_SEATS = 1000;
export const HHMM = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
const INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;
// Unicode category Cc, which `core.models` refuses in any text.
// oxlint-disable-next-line no-control-regex -- finding them is the point
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;

/** The platform's models forbid unknown fields. */
export function exact(body: object, allowed: readonly string[]): void {
  const extra = Object.keys(body).filter((key) => !allowed.includes(key));
  if (extra.length) invalid(`${extra.join(", ")}: Extra inputs are not permitted`);
}

/** A stripped text without a control character; blank is null unless required. */
export function text(value: unknown, field: string, max: number, required: boolean): string | null {
  if (value === null || value === undefined) {
    return required ? invalid(`${field}: Field required`) : null;
  }
  if (typeof value !== "string") return invalid(`${field}: a string is required`);
  const trimmed = value.trim();
  if (CONTROL.test(trimmed)) invalid(`${field}: must not contain a control character`);
  if (trimmed.length > max) invalid(`${field}: at most ${max} characters`);
  if (!trimmed && required) invalid(`${field}: must not be blank`);
  return trimmed || null;
}

export function required(value: unknown, field: string, max: number): string {
  return text(value, field, max, true) ?? invalid(`${field}: Field required`);
}

/** A time with its UTC offset, as `BookingTime` (`core.models`) reads it. */
export function aware(value: unknown): string {
  if (typeof value !== "string" || !INSTANT.test(value) || Number.isNaN(Date.parse(value))) {
    return invalid("starts_at: a time with its UTC offset is required");
  }
  // Between 2000-01-01 and 2099-12-31.
  const year = Number(value.slice(0, 4));
  if (year < 2000 || year > 2099) invalid("starts_at: must be between 2000-01-01 and 2099-12-31");
  return value;
}

export function parseUrl(value: unknown): URL | null {
  try {
    return typeof value === "string" ? new URL(value) : null;
  } catch {
    return null;
  }
}

/**
 * agent/admin.py `_same_site`: the host, `www.` aside, and the port, the scheme's by default, so
 * the demo sites differ only by their port.
 */
export function sameSite(page: URL, website: string): boolean {
  const home = parseUrl(website);
  return home !== null && site(page) !== null && site(page) === site(home);
}

function site(url: URL): string | null {
  const host = url.hostname.replace(/^www\./, "");
  return host ? `${host}:${url.port || (url.protocol === "https:" ? "443" : "80")}` : null;
}
