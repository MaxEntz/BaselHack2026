// The site's own changes, as a client event (#137). `/events` only sends guests'
// changes (#81), so a mutation of a page, or a tool the assistant ran, says so
// here, and the shell reads the pages again.
import type { ManagementClient } from "./client.ts";

const EVENT = "sesame:changed";

type ChangeDetail = { slug: string };

/** Something changed `slug`'s data: the pages read it again. The assistant (#140) calls it after a tool. */
export function announceChange(slug: string): void {
  window.dispatchEvent(new CustomEvent<ChangeDetail>(EVENT, { detail: { slug } }));
}

/** Runs `listener` on every change of `slug`; returns the unsubscribe function. */
export function onChange(slug: string, listener: () => void): () => void {
  const handle = (event: Event) => {
    if ((event as CustomEvent<ChangeDetail>).detail?.slug === slug) listener();
  };
  window.addEventListener(EVENT, handle);
  return () => window.removeEventListener(EVENT, handle);
}

// The calls that change something, checked against the client's keys: a renamed
// call fails to compile, a new mutation must be added here.
const MUTATIONS = [
  "addBooking",
  "moveBooking",
  "cancelBooking",
  "saveHours",
  "saveTables",
  "saveRules",
  "saveSettings",
  "setBlocked",
  "pauseAgent",
  "resumeAgent",
] as const satisfies readonly (keyof ManagementClient)[];

type Mutation = (slug: string, ...rest: never[]) => Promise<unknown>;

/** `client`, announcing a change after every mutation that succeeds. */
export function announcingChanges(client: ManagementClient): ManagementClient {
  const wrapped: Record<string, unknown> = { ...client };
  for (const name of MUTATIONS) {
    const call = client[name] as Mutation;
    wrapped[name] = async (slug: string, ...rest: never[]) => {
      const answer = await call(slug, ...rest);
      announceChange(slug);
      return answer;
    };
  }
  return wrapped as ManagementClient;
}
