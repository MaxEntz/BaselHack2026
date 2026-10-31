import { createContext, useContext } from "react";
import { type ChatClient, httpClient } from "../../chat/client.ts";
import { createMockClient } from "../../mocks/mockClient.ts";

// The platform's `/chat` for the assistant panel (#140); VITE_MOCK=1: the chat's own script.
const DEFAULT: ChatClient = import.meta.env.VITE_MOCK === "1" ? createMockClient(true) : httpClient;

export const ChatClientContext = createContext<ChatClient>(DEFAULT);

export function useChatClient(): ChatClient {
  return useContext(ChatClientContext);
}
