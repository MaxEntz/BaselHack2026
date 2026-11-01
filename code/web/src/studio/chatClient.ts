import { type ChatClient, httpClient } from "../chat/client.ts";
import type { Merchant } from "../chat/state.ts";

// VITE_MERCHANT_SLUG skips onboarding: the chat starts as this merchant.
export const DEMO_MERCHANT: Merchant | null = import.meta.env.VITE_MERCHANT_SLUG
  ? {
      slug: import.meta.env.VITE_MERCHANT_SLUG,
      name: import.meta.env.VITE_MERCHANT_NAME || import.meta.env.VITE_MERCHANT_SLUG,
    }
  : null;

// Studio's `/chat`, or with VITE_MOCK=1 the replayed conversation. The mock is imported only
// then, and `import.meta.env` is read whole at each use, so that a production build can drop
// the mock and its data (#220).
export const CHAT_CLIENT: ChatClient =
  import.meta.env.VITE_MOCK === "1"
    ? (await import("../mocks/mockClient.ts")).createMockClient(DEMO_MERCHANT !== null)
    : httpClient;
