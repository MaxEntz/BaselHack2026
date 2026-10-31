// The site's own changes, as a client event (#137). `/events` only sends guests'
// changes (#81), so a mutation of a page, or a tool the assistant ran, says so
// here, and the shell reads the pages again.
import type { ManagementClient } from "./client.ts";

const NAME = "sesame:changes";

type ChangeDetail = { slug: string };

/**
 * Something changed `slug`'s data: the pages read it again, in every window of the site
 * (another tab, Studio's frame). The chat calls it after a tool that changed something.
 */
export function announceChange(slug: string): void {
  if (typeof BroadcastChannel === "undefined") {
    window.dispatchEvent(new CustomEvent<ChangeDetail>(NAME, { detail: { slug } }));
    return;
  }
  // Every other channel of this name hears it, those of this window too.
  const channel = new BroadcastChannel(NAME);
  channel.postMessage({ slug } satisfies ChangeDetail);
  channel.close();
}

/** Runs `listener` on every change of `slug`; returns the unsubscribe function. */
export function onChange(slug: string, listener: () => void): () => void {
  const hear = (detail?: ChangeDetail) => {
    if (detail?.slug === slug) listener();
  };
  if (typeof BroadcastChannel === "undefined") {
    const handle = (event: Event) => hear((event as CustomEvent<ChangeDetail>).detail);
    window.addEventListener(NAME, handle);
    return () => window.removeEventListener(NAME, handle);
  }
  const channel = new BroadcastChannel(NAME);
  channel.onmessage = (event: MessageEvent<ChangeDetail>) => hear(event.data);
  return () => channel.close();
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
