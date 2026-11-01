import { createContext, useContext } from "react";
import { type ChatClient, httpClient } from "../../chat/client.ts";

// The platform's `/chat` for the assistant panel (#140); VITE_MOCK=1: the chat's own script,
// imported only then so that a production build drops it (#220).
const DEFAULT: ChatClient =
  import.meta.env.VITE_MOCK === "1"
    ? (await import("../../mocks/mockClient.ts")).createMockClient(true)
    : httpClient;

export const ChatClientContext = createContext<ChatClient>(DEFAULT);

export function useChatClient(): ChatClient {
  return useContext(ChatClientContext);
}
